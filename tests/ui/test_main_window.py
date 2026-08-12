import json
import wave
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox
from pytestqt.qtbot import QtBot

from meeting_transcriber.app.recording_service import RecordingLevels, RecordingStopResult
from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.app.storage_health import (
    BYTES_PER_GIBIBYTE,
    DiskSpaceStatus,
    StorageHealth,
)
from meeting_transcriber.capture.devices import (
    AudioDevice,
    AudioDeviceCatalog,
    AudioDeviceKind,
)
from meeting_transcriber.capture.manifest import CaptureJournalState, CaptureManifest
from meeting_transcriber.domain.session import MeetingSession, SessionState
from meeting_transcriber.domain.transcript import (
    TranscriptDocument,
    TranscriptionJob,
    TranscriptionJobState,
    TranscriptionProfile,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
)
from meeting_transcriber.storage.meeting_notes_store import MeetingNotesStore
from meeting_transcriber.storage.review_store import ReviewStore
from meeting_transcriber.storage.session_store import SessionStore
from meeting_transcriber.storage.transcript_store import TranscriptStore
from meeting_transcriber.ui.main_window import MainWindow


class FakeAudioDiscovery:
    def discover_devices(self) -> AudioDeviceCatalog:
        microphone = AudioDevice(
            device_id="microphone",
            backend_index=1,
            name="Test microphone",
            kind=AudioDeviceKind.MICROPHONE,
            host_api="Test",
            max_input_channels=1,
            default_sample_rate=48_000,
        )
        loopback = AudioDevice(
            device_id="loopback",
            backend_index=2,
            name="Test speakers [Loopback]",
            kind=AudioDeviceKind.SYSTEM_LOOPBACK,
            host_api="Test",
            max_input_channels=2,
            default_sample_rate=48_000,
        )
        return AudioDeviceCatalog((microphone,), (loopback,))


class FakeRecordingWorkflow:
    def __init__(self, sessions: MeetingSessionService):
        self.sessions = sessions
        self.discovery = FakeAudioDiscovery()
        self.start_calls: list[tuple[str, str, str, bool]] = []
        self.preflight_calls: list[tuple[str, str, str, bool]] = []
        self._recording: bool = False
        self._preflighting: bool = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_preflighting(self) -> bool:
        return self._preflighting

    def discover_devices(self) -> AudioDeviceCatalog:
        return self.discovery.discover_devices()

    def storage_status(self) -> DiskSpaceStatus:
        total = 100 * BYTES_PER_GIBIBYTE
        free = 10 * BYTES_PER_GIBIBYTE
        return DiskSpaceStatus(total, total - free, free, StorageHealth.HEALTHY)

    def start_preflight(
        self,
        session_id: str,
        microphone_id: str,
        loopback_id: str,
        *,
        consent_confirmed: bool,
    ) -> None:
        self.preflight_calls.append((session_id, microphone_id, loopback_id, consent_confirmed))
        assert consent_confirmed
        self.sessions.confirm_recording_consent(session_id)
        self._preflighting = True

    def stop_preflight(self) -> None:
        assert self._preflighting
        self._preflighting = False

    def start(
        self,
        session_id: str,
        microphone_id: str,
        loopback_id: str,
        *,
        consent_confirmed: bool,
    ) -> MeetingSession:
        self.start_calls.append((session_id, microphone_id, loopback_id, consent_confirmed))
        assert consent_confirmed
        self.sessions.confirm_recording_consent(session_id)
        self._recording = True
        return self.sessions.transition_state(session_id, SessionState.RECORDING)

    def stop(self) -> RecordingStopResult:
        session = self.sessions.recent_sessions()[0]
        recorded = self.sessions.transition_state(session.session_id, SessionState.RECORDED)
        self._recording = False
        return RecordingStopResult(
            recorded,
            CaptureManifest(
                schema_version=1,
                session_id=recorded.session_id,
                state=CaptureJournalState.STOPPED,
                started_monotonic_ns=1,
                updated_monotonic_ns=2,
                stopped_monotonic_ns=2,
                sources=(),
            ),
        )

    def latest_levels(self) -> RecordingLevels:
        return RecordingLevels(microphone=0.2, system_audio=0.8)

    def pause(self) -> MeetingSession:
        session = self.sessions.recent_sessions()[0]
        return self.sessions.transition_state(session.session_id, SessionState.PAUSED)

    def resume(self) -> MeetingSession:
        session = self.sessions.recent_sessions()[0]
        return self.sessions.transition_state(session.session_id, SessionState.RECORDING)


class FakeTranscriptionWorkflow:
    def __init__(self, sessions: MeetingSessionService):
        self.sessions = sessions
        self.job: TranscriptionJob | None = None
        self._processing: bool = False

    @property
    def is_processing(self) -> bool:
        return self._processing

    def recover_interrupted_jobs(self) -> list[TranscriptionJob]:
        return []

    def job_for(self, session_id: str) -> TranscriptionJob | None:
        if self.job is not None and self.job.session_id == session_id:
            return self.job
        return None

    def current_job(self) -> TranscriptionJob | None:
        return self.job

    def start(
        self,
        session_id: str,
        *,
        profile: TranscriptionProfile,
        language: str | None,
        hotwords: str | None,
        allow_download: bool,
    ) -> TranscriptionJob:
        del hotwords, allow_download
        job = TranscriptionJob.new(session_id, profile=profile, language=language)
        job = job.transition(TranscriptionJobState.PREPARING)
        job = job.with_progress(0, 2_000)
        self.job = job.transition(TranscriptionJobState.TRANSCRIBING)
        self.sessions.transition_state(session_id, SessionState.PROCESSING)
        self._processing = True
        return self.job

    def cancel(self) -> None:
        if self.job is None:
            raise AssertionError("No fake transcription is running")
        self.job = self.job.transition(TranscriptionJobState.CANCELLED)
        self.sessions.transition_state(self.job.session_id, SessionState.RECORDED)
        self._processing = False

    def complete(self) -> None:
        if self.job is None:
            raise AssertionError("No fake transcription is running")
        self.sessions.transition_state(self.job.session_id, SessionState.READY)
        self.job = self.job.transition(TranscriptionJobState.COMPLETED)
        transcript = TranscriptDocument.new(
            self.job.session_id,
            run_id=self.job.job_id,
            language="en",
            engine="fixture",
            model="fixture",
            profile=self.job.profile,
            created_at=datetime.now(UTC),
            speakers=(
                TranscriptSpeaker("local", "You", TranscriptSource.MICROPHONE),
                TranscriptSpeaker("remote", "Remote speakers", TranscriptSource.SYSTEM_AUDIO),
            ),
            segments=(
                TranscriptSegment(
                    "808a9df2-1435-41aa-b7a7-7ee09f00a138",
                    0,
                    1_000,
                    "remote",
                    "Project at less",
                    TranscriptSource.SYSTEM_AUDIO,
                ),
            ),
        )
        TranscriptStore(self.sessions.store.root).save_transcript(transcript)
        MeetingNotesStore(self.sessions.store.root).save(
            self.job.session_id,
            self.job.job_id,
            "# Fake meeting notes\n",
        )
        self._processing = False


def test_main_window_exposes_home_and_diagnostics_pages(qtbot: QtBot, tmp_path: Path) -> None:
    service = MeetingSessionService(SessionStore(tmp_path))
    window = MainWindow(service)
    qtbot.addWidget(window)

    window.show()

    assert window.isVisible()
    assert window.windowTitle() == "Meeting Transcriber"
    assert window.pages.count() == 6
    assert window.pages.currentWidget() is window.home_page


def test_create_draft_button_persists_a_named_session(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MeetingSessionService(SessionStore(tmp_path))
    window = MainWindow(service, FakeAudioDiscovery())
    qtbot.addWidget(window)

    def fake_get_text(*args: object, **kwargs: object) -> tuple[str, bool]:
        return "Architecture review", True

    def fake_information(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QInputDialog, "getText", fake_get_text)
    monkeypatch.setattr(QMessageBox, "information", fake_information)

    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.home_page.start_button,
        Qt.MouseButton.LeftButton,
    )

    sessions = service.recent_sessions()
    assert len(sessions) == 1
    assert sessions[0].title == "Architecture review"
    assert window.pages.currentWidget() is window.recording_page
    assert not window.recording_page.begin_button.isEnabled()
    assert "review recording setup" in window.statusBar().currentMessage()
    assert "GiB free" in window.recording_page.storage_status_label.text()
    assert window.storage_timer.isActive()


def test_diagnostics_refreshes_audio_devices_explicitly(qtbot: QtBot, tmp_path: Path) -> None:
    service = MeetingSessionService(SessionStore(tmp_path))
    window = MainWindow(service, FakeAudioDiscovery())
    qtbot.addWidget(window)

    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.diagnostics_page.refresh_audio_button,
        Qt.MouseButton.LeftButton,
    )

    value = window.diagnostics_page.audio_card.value_label.text()
    assert "Test microphone" in value
    assert "Test speakers [Loopback]" in value


def test_consent_gated_ui_starts_and_stops_visible_recording(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MeetingSessionService(SessionStore(tmp_path))
    workflow = FakeRecordingWorkflow(service)
    window = MainWindow(service, FakeAudioDiscovery(), workflow)
    qtbot.addWidget(window)

    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *args, **kwargs: ("Client interview", True),
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )

    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.home_page.start_button,
        Qt.MouseButton.LeftButton,
    )
    assert workflow.start_calls == []

    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.recording_page.consent_checkbox,
        Qt.MouseButton.LeftButton,
    )
    assert workflow.start_calls == []

    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.recording_page.begin_button,
        Qt.MouseButton.LeftButton,
    )

    recording = service.recent_sessions()[0]
    assert workflow.start_calls == [(recording.session_id, "microphone", "loopback", True)]
    assert recording.state is SessionState.RECORDING
    assert recording.has_current_recording_consent
    assert not window.recording_page.recording_card.isHidden()
    assert not window.global_recording_indicator.isHidden()
    assert not window.home_button.isEnabled()
    assert window.recording_page.microphone_level.value() == 20
    assert window.recording_page.system_audio_level.value() == 80
    assert "10.0 GiB free" in window.recording_page.live_storage_label.text()

    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.recording_page.pause_button,
        Qt.MouseButton.LeftButton,
    )

    assert service.recent_sessions()[0].state is SessionState.PAUSED
    assert window.recording_page.recording_pill.text() == "Ⅱ PAUSED"
    assert window.recording_page.pause_button.text() == "Resume recording"
    assert window.recording_page.microphone_level.value() == 0

    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.recording_page.pause_button,
        Qt.MouseButton.LeftButton,
    )

    assert service.recent_sessions()[0].state is SessionState.RECORDING
    assert window.recording_page.recording_pill.text() == "● RECORDING"
    assert window.recording_page.pause_button.text() == "Pause recording"

    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.recording_page.stop_button,
        Qt.MouseButton.LeftButton,
    )

    assert service.recent_sessions()[0].state is SessionState.RECORDED
    assert window.recording_page.recording_card.isHidden()
    assert window.global_recording_indicator.isHidden()
    assert window.home_button.isEnabled()
    assert window.pages.currentWidget() is window.home_page


def test_consent_gated_source_test_is_timed_and_writes_no_recording_state(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MeetingSessionService(SessionStore(tmp_path))
    workflow = FakeRecordingWorkflow(service)
    window = MainWindow(service, FakeAudioDiscovery(), workflow)
    qtbot.addWidget(window)
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *args, **kwargs: ("Source test", True),
    )

    qtbot.mouseClick(window.home_page.start_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]
    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.recording_page.consent_checkbox,
        Qt.MouseButton.LeftButton,
    )
    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.recording_page.preflight_button,
        Qt.MouseButton.LeftButton,
    )

    draft = service.recent_sessions()[0]
    assert workflow.preflight_calls == [(draft.session_id, "microphone", "loopback", True)]
    assert draft.state is SessionState.DRAFT
    assert service.get_session(draft.session_id).has_current_recording_consent
    assert workflow.is_preflighting
    assert window.preflight_timeout.isActive()
    assert window.recording_page.preflight_microphone_level.value() == 20
    assert window.recording_page.preflight_system_level.value() == 80
    assert not window.recording_page.begin_button.isEnabled()

    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.recording_page.preflight_button,
        Qt.MouseButton.LeftButton,
    )

    assert not window.preflight_timeout.isActive()
    assert window.recording_page.begin_button.isEnabled()
    assert window.recording_page.preflight_microphone_level.value() == 0


def test_history_recovers_audio_and_opens_session_folder(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MeetingSessionService(SessionStore(tmp_path))
    draft = service.create_draft("Interrupted interview")
    service.confirm_recording_consent(draft.session_id)
    service.transition_state(draft.session_id, SessionState.RECORDING)
    service.transition_state(draft.session_id, SessionState.INTERRUPTED)
    directory = service.session_directory(draft.session_id)
    (directory / "capture.json").write_text(
        json.dumps({"session_id": draft.session_id}),
        encoding="utf-8",
    )
    audio_directory = directory / "audio"
    audio_directory.mkdir()
    with wave.open(str(audio_directory / "microphone_0001.wav"), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(48_000)
        wav_file.writeframes(b"\x00\x00" * 8)
    opened: list[Path] = []

    def fake_open_folder(path: Path) -> bool:
        opened.append(path)
        return True

    workflow = FakeRecordingWorkflow(service)
    window = MainWindow(
        service,
        FakeAudioDiscovery(),
        workflow,
        fake_open_folder,
    )
    qtbot.addWidget(window)
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )

    qtbot.mouseClick(window.history_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]

    assert window.pages.currentWidget() is window.history_page
    assert window.history_page.recover_button.isEnabled()

    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.history_page.open_folder_button,
        Qt.MouseButton.LeftButton,
    )
    assert opened == [directory]

    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.history_page.recover_button,
        Qt.MouseButton.LeftButton,
    )

    assert service.get_session(draft.session_id).state is SessionState.RECORDED
    assert not window.history_page.recover_button.isEnabled()


def test_history_launches_and_completes_offline_transcription(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = MeetingSessionService(SessionStore(tmp_path))
    session_id = sessions.create_draft("Transcript me").session_id
    sessions.confirm_recording_consent(session_id)
    sessions.transition_state(session_id, SessionState.RECORDING)
    sessions.transition_state(session_id, SessionState.RECORDED)
    recording = FakeRecordingWorkflow(sessions)
    transcription = FakeTranscriptionWorkflow(sessions)
    opened: list[Path] = []

    def fake_open_path(path: Path) -> bool:
        opened.append(path)
        return True

    window = MainWindow(
        sessions,
        FakeAudioDiscovery(),
        recording,
        folder_opener=fake_open_path,
        transcription_service=transcription,
    )
    qtbot.addWidget(window)
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )

    qtbot.mouseClick(window.history_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]
    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.history_page.transcribe_button,
        Qt.MouseButton.LeftButton,
    )

    assert window.pages.currentWidget() is window.transcription_page
    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.transcription_page.start_button,
        Qt.MouseButton.LeftButton,
    )

    assert transcription.is_processing
    assert sessions.get_session(session_id).state is SessionState.PROCESSING
    assert not window.transcription_page.progress_card.isHidden()
    assert not window.home_button.isEnabled()

    transcription.complete()
    window._poll_transcription()

    assert sessions.get_session(session_id).state is SessionState.READY
    assert window.transcription_page.start_button.text() == "Transcribe again"
    assert window.home_button.isEnabled()
    assert "Markdown meeting notes saved" in window.statusBar().currentMessage()
    qtbot.mouseClick(window.history_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]
    assert window.history_page.open_notes_button.isEnabled()
    assert window.history_page.review_button.isEnabled()
    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.history_page.open_notes_button,
        Qt.MouseButton.LeftButton,
    )
    assert opened == [tmp_path / session_id / "meeting-notes.md"]

    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.history_page.review_button,
        Qt.MouseButton.LeftButton,
    )
    assert window.pages.currentWidget() is window.review_page
    window.review_page.speaker_combo.setCurrentIndex(1)
    window.review_page.speaker_name_input.setText("Morgan")
    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.review_page.save_speaker_button,
        Qt.MouseButton.LeftButton,
    )
    window.review_page.segment_text_edit.setPlainText("Project Atlas")
    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.review_page.save_segment_button,
        Qt.MouseButton.LeftButton,
    )

    review = ReviewStore(tmp_path).load(session_id)
    markdown = MeetingNotesStore(tmp_path).notes_file(session_id).read_text(encoding="utf-8")
    assert review.revision == 2
    assert "Morgan" in markdown
    assert "Project Atlas" in markdown

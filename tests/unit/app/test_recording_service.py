from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from meeting_transcriber.app.recording_service import (
    MeetingRecordingService,
    RecordingConsentRequired,
    RecordingDeviceUnavailable,
    RecordingStartError,
    RecordingStopError,
)
from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.capture.devices import (
    AudioDevice,
    AudioDeviceCatalog,
    AudioDeviceKind,
)
from meeting_transcriber.capture.levels import AudioLevelSnapshot
from meeting_transcriber.capture.manifest import CaptureJournalState, CaptureManifest
from meeting_transcriber.capture.streams import (
    AudioInputStream,
    AudioStreamFactory,
    SourceCaptureConfig,
)
from meeting_transcriber.domain.session import SessionState
from meeting_transcriber.storage.session_store import SessionStore


class FakeDiscovery:
    def __init__(self, catalog: AudioDeviceCatalog):
        self.catalog = catalog
        self.calls = 0

    def discover_devices(self) -> AudioDeviceCatalog:
        self.calls += 1
        return self.catalog


class UnusedStreamFactory:
    def open_input(self, config: SourceCaptureConfig) -> AudioInputStream:
        raise AssertionError(f"Fake coordinated capture should own {config.device.name}")


class FakeCapture:
    def __init__(
        self,
        session_id: str,
        *,
        fail_start: bool = False,
        fail_stop: bool = False,
        stop_state: CaptureJournalState = CaptureJournalState.STOPPED,
        on_audio_level: Callable[[AudioLevelSnapshot], None] | None = None,
    ):
        self.session_id = session_id
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self.stop_state = stop_state
        self.on_audio_level = on_audio_level
        self.started = False
        self.paused = False
        self.stopped = False

    def start(self) -> CaptureManifest:
        if self.fail_start:
            raise OSError("Synthetic device failure")
        self.started = True
        if self.on_audio_level is not None:
            self.on_audio_level(AudioLevelSnapshot(AudioDeviceKind.MICROPHONE, 0.25, 1))
            self.on_audio_level(AudioLevelSnapshot(AudioDeviceKind.SYSTEM_LOOPBACK, 0.75, 1))
        return _manifest(self.session_id, CaptureJournalState.RECORDING)

    def stop(self, *, timeout_seconds: float = 5.0) -> CaptureManifest:
        assert timeout_seconds == 5.0
        if self.fail_stop:
            raise OSError("Synthetic shutdown failure")
        self.stopped = True
        return _manifest(self.session_id, self.stop_state)

    def pause(self, *, timeout_seconds: float = 2.0) -> None:
        assert timeout_seconds == 2.0
        self.paused = True

    def resume(self) -> None:
        assert self.paused
        self.paused = False


class FakeCaptureFactory:
    def __init__(
        self,
        session_service: MeetingSessionService,
        *,
        fail_start: bool = False,
        fail_stop: bool = False,
        stop_state: CaptureJournalState = CaptureJournalState.STOPPED,
    ):
        self.session_service = session_service
        self.fail_start = fail_start
        self.fail_stop = fail_stop
        self.stop_state = stop_state
        self.calls: list[tuple[SourceCaptureConfig, SourceCaptureConfig]] = []
        self.capture: FakeCapture | None = None

    def __call__(
        self,
        *,
        session_id: str,
        session_directory: Path,
        configs: tuple[SourceCaptureConfig, SourceCaptureConfig],
        stream_factory: AudioStreamFactory,
        on_audio_level: Callable[[AudioLevelSnapshot], None],
    ) -> FakeCapture:
        del stream_factory
        persisted = self.session_service.get_session(session_id)
        assert persisted.state is SessionState.RECORDING
        assert persisted.has_current_recording_consent
        assert session_directory == self.session_service.store.session_directory(session_id)
        self.calls.append(configs)
        self.capture = FakeCapture(
            session_id,
            fail_start=self.fail_start,
            fail_stop=self.fail_stop,
            stop_state=self.stop_state,
            on_audio_level=on_audio_level,
        )
        return self.capture


def _device(kind: AudioDeviceKind, device_id: str, channels: int) -> AudioDevice:
    return AudioDevice(
        device_id=device_id,
        backend_index=1,
        name=device_id,
        kind=kind,
        host_api="Test",
        max_input_channels=channels,
        default_sample_rate=48_000,
        is_default=True,
    )


def _catalog() -> AudioDeviceCatalog:
    return AudioDeviceCatalog(
        microphones=(_device(AudioDeviceKind.MICROPHONE, "mic", 2),),
        loopbacks=(_device(AudioDeviceKind.SYSTEM_LOOPBACK, "loopback", 8),),
    )


def _manifest(session_id: str, state: CaptureJournalState) -> CaptureManifest:
    return CaptureManifest(
        schema_version=1,
        session_id=session_id,
        state=state,
        started_monotonic_ns=1,
        updated_monotonic_ns=2,
        stopped_monotonic_ns=2 if state is not CaptureJournalState.RECORDING else None,
        sources=(),
    )


def _service(
    tmp_path: Path,
    *,
    catalog: AudioDeviceCatalog | None = None,
    fail_start: bool = False,
    fail_stop: bool = False,
    stop_state: CaptureJournalState = CaptureJournalState.STOPPED,
) -> tuple[MeetingRecordingService, MeetingSessionService, FakeDiscovery, FakeCaptureFactory]:
    sessions = MeetingSessionService(SessionStore(tmp_path))
    discovery = FakeDiscovery(catalog or _catalog())
    captures = FakeCaptureFactory(
        sessions,
        fail_start=fail_start,
        fail_stop=fail_stop,
        stop_state=stop_state,
    )
    service = MeetingRecordingService(
        sessions,
        discovery,
        UnusedStreamFactory(),
        captures,
    )
    return service, sessions, discovery, captures


def test_missing_ui_acknowledgement_opens_nothing(tmp_path: Path) -> None:
    service, sessions, discovery, captures = _service(tmp_path)
    draft = sessions.create_draft("Weekly sync")

    with pytest.raises(RecordingConsentRequired, match="Confirm participant"):
        service.start(
            draft.session_id,
            "mic",
            "loopback",
            consent_confirmed=False,
        )

    persisted = sessions.get_session(draft.session_id)
    assert discovery.calls == 0
    assert captures.calls == []
    assert persisted.state is SessionState.DRAFT
    assert persisted.consent_confirmed_at is None


def test_start_persists_consent_before_capture_and_stop_records_session(tmp_path: Path) -> None:
    service, sessions, discovery, captures = _service(tmp_path)
    draft = sessions.create_draft("Weekly sync")

    recording = service.start(
        draft.session_id,
        "mic",
        "loopback",
        consent_confirmed=True,
    )
    result = service.stop()

    assert discovery.calls == 1
    assert recording.state is SessionState.RECORDING
    assert recording.has_current_recording_consent
    assert len(captures.calls) == 1
    assert [config.audio_format.channels for config in captures.calls[0]] == [1, 2]
    assert captures.capture is not None
    assert captures.capture.started and captures.capture.stopped
    assert service.latest_levels().microphone == 0.25
    assert service.latest_levels().system_audio == 0.75
    assert result.session.state is SessionState.RECORDED
    assert sessions.get_session(draft.session_id) == result.session
    assert not service.is_recording


def test_missing_selected_device_does_not_persist_consent(tmp_path: Path) -> None:
    service, sessions, _discovery, captures = _service(tmp_path)
    draft = sessions.create_draft("Weekly sync")

    with pytest.raises(RecordingDeviceUnavailable, match="no longer available"):
        service.start(
            draft.session_id,
            "disconnected-microphone",
            "loopback",
            consent_confirmed=True,
        )

    assert captures.calls == []
    assert sessions.get_session(draft.session_id).consent_confirmed_at is None


def test_pause_and_resume_persist_capture_and_session_state(tmp_path: Path) -> None:
    service, sessions, _discovery, captures = _service(tmp_path)
    draft = sessions.create_draft("Weekly sync")
    service.start(draft.session_id, "mic", "loopback", consent_confirmed=True)

    paused = service.pause()

    assert paused.state is SessionState.PAUSED
    assert captures.capture is not None and captures.capture.paused
    assert service.latest_levels().microphone == 0.0
    assert service.latest_levels().system_audio == 0.0

    resumed = service.resume()

    assert resumed.state is SessionState.RECORDING
    service.stop()


def test_capture_start_failure_marks_session_interrupted(tmp_path: Path) -> None:
    service, sessions, _discovery, _captures = _service(tmp_path, fail_start=True)
    draft = sessions.create_draft("Weekly sync")

    with pytest.raises(RecordingStartError, match="could not start"):
        service.start(
            draft.session_id,
            "mic",
            "loopback",
            consent_confirmed=True,
        )

    assert sessions.get_session(draft.session_id).state is SessionState.INTERRUPTED
    assert not service.is_recording


def test_capture_stop_failure_marks_session_interrupted(tmp_path: Path) -> None:
    service, sessions, _discovery, _captures = _service(tmp_path, fail_stop=True)
    draft = sessions.create_draft("Weekly sync")
    service.start(draft.session_id, "mic", "loopback", consent_confirmed=True)

    with pytest.raises(RecordingStopError, match="could not stop"):
        service.stop()

    assert sessions.get_session(draft.session_id).state is SessionState.INTERRUPTED
    assert not service.is_recording


def test_interrupted_capture_manifest_marks_session_interrupted(tmp_path: Path) -> None:
    service, sessions, _discovery, _captures = _service(
        tmp_path,
        stop_state=CaptureJournalState.INTERRUPTED,
    )
    draft = sessions.create_draft("Weekly sync")
    service.start(draft.session_id, "mic", "loopback", consent_confirmed=True)

    result = service.stop()

    assert result.session.state is SessionState.INTERRUPTED
    assert not service.is_recording

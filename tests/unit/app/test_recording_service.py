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
    RecordingStorageCritical,
    RecordingWorkflowError,
)
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


class FakeStorageChecker:
    def __init__(self, status: DiskSpaceStatus):
        self.status = status
        self.calls = 0

    def check(self) -> DiskSpaceStatus:
        self.calls += 1
        return self.status


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


class FakePreflight:
    def __init__(self, on_audio_level: Callable[[AudioLevelSnapshot], None]):
        self.on_audio_level = on_audio_level
        self.started: bool = False
        self.stopped: bool = False

    def start(self) -> None:
        self.started = True
        self.on_audio_level(AudioLevelSnapshot(AudioDeviceKind.MICROPHONE, 0.4, 1))
        self.on_audio_level(AudioLevelSnapshot(AudioDeviceKind.SYSTEM_LOOPBACK, 0.6, 1))

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        assert timeout_seconds == 2.0
        self.stopped = True


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
        self.preflight_calls: list[tuple[SourceCaptureConfig, SourceCaptureConfig]] = []
        self.preflight: FakePreflight | None = None

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

    def build_preflight(
        self,
        *,
        configs: tuple[SourceCaptureConfig, SourceCaptureConfig],
        stream_factory: AudioStreamFactory,
        on_audio_level: Callable[[AudioLevelSnapshot], None],
    ) -> FakePreflight:
        del stream_factory
        persisted = self.session_service.recent_sessions()[0]
        assert persisted.state is SessionState.DRAFT
        assert persisted.has_current_recording_consent
        self.preflight_calls.append(configs)
        self.preflight = FakePreflight(on_audio_level)
        return self.preflight


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


def _storage_status(
    health: StorageHealth = StorageHealth.HEALTHY,
    free_bytes: int = 10 * BYTES_PER_GIBIBYTE,
) -> DiskSpaceStatus:
    total = 100 * BYTES_PER_GIBIBYTE
    return DiskSpaceStatus(total, total - free_bytes, free_bytes, health)


def _service(
    tmp_path: Path,
    *,
    catalog: AudioDeviceCatalog | None = None,
    fail_start: bool = False,
    fail_stop: bool = False,
    stop_state: CaptureJournalState = CaptureJournalState.STOPPED,
    storage_status: DiskSpaceStatus | None = None,
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
        captures.build_preflight,
        FakeStorageChecker(storage_status or _storage_status()),
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


def test_critical_storage_blocks_recording_before_consent_or_device_refresh(
    tmp_path: Path,
) -> None:
    critical = _storage_status(StorageHealth.CRITICAL, BYTES_PER_GIBIBYTE // 2)
    service, sessions, discovery, captures = _service(tmp_path, storage_status=critical)
    draft = sessions.create_draft("Weekly sync")

    with pytest.raises(RecordingStorageCritical, match="Critical storage"):
        service.start(draft.session_id, "mic", "loopback", consent_confirmed=True)

    assert discovery.calls == 0
    assert captures.calls == []
    assert sessions.get_session(draft.session_id).consent_confirmed_at is None


def test_missing_ui_acknowledgement_blocks_source_preflight(tmp_path: Path) -> None:
    service, sessions, discovery, captures = _service(tmp_path)
    draft = sessions.create_draft("Weekly sync")

    with pytest.raises(RecordingConsentRequired, match="Confirm participant"):
        service.start_preflight(
            draft.session_id,
            "mic",
            "loopback",
            consent_confirmed=False,
        )

    assert discovery.calls == 0
    assert captures.preflight_calls == []
    assert sessions.get_session(draft.session_id).consent_confirmed_at is None


def test_preflight_persists_consent_reads_levels_and_writes_no_recording_state(
    tmp_path: Path,
) -> None:
    service, sessions, discovery, captures = _service(tmp_path)
    draft = sessions.create_draft("Weekly sync")

    service.start_preflight(
        draft.session_id,
        "mic",
        "loopback",
        consent_confirmed=True,
    )

    persisted = sessions.get_session(draft.session_id)
    assert discovery.calls == 1
    assert persisted.state is SessionState.DRAFT
    assert persisted.has_current_recording_consent
    assert service.is_preflighting
    assert service.latest_levels().microphone == 0.4
    assert service.latest_levels().system_audio == 0.6
    assert captures.preflight is not None and captures.preflight.started
    assert list(sessions.session_directory(draft.session_id).glob("audio/*.wav")) == []

    with pytest.raises(RecordingWorkflowError, match="Stop the audio source test"):
        service.start(draft.session_id, "mic", "loopback", consent_confirmed=True)

    service.stop_preflight()

    assert captures.preflight.stopped
    assert service.latest_levels().microphone == 0.0


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

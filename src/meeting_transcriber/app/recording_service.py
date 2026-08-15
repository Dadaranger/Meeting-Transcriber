from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol

from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.app.storage_health import (
    DiskSpaceChecker,
    DiskSpaceStatus,
    StorageHealth,
)
from meeting_transcriber.capture.devices import (
    AudioDevice,
    AudioDeviceCatalog,
    AudioDeviceDiscovery,
    AudioDeviceKind,
    DeviceDiscoveryError,
)
from meeting_transcriber.capture.formats import AudioFormat
from meeting_transcriber.capture.levels import AudioLevelSnapshot
from meeting_transcriber.capture.manifest import (
    CaptureJournalState,
    CaptureManifest,
)
from meeting_transcriber.capture.preflight import AudioSourcePreflight
from meeting_transcriber.capture.recorder import DualSourceCapture
from meeting_transcriber.capture.streams import AudioStreamFactory, SourceCaptureConfig
from meeting_transcriber.domain.session import MeetingSession, SessionState


class RecordingWorkflowError(RuntimeError):
    """Raised when the desktop recording workflow cannot proceed safely."""


class RecordingConsentRequired(RecordingWorkflowError):
    """Raised before any stream opens when the explicit UI acknowledgement is absent."""


class RecordingDeviceUnavailable(RecordingWorkflowError):
    """Raised when a selected capture endpoint is no longer available."""


class RecordingStartError(RecordingWorkflowError):
    """Raised after a capture startup failure has been persisted as interrupted."""


class RecordingPreflightError(RecordingWorkflowError):
    """Raised when the consent-gated source test cannot start or stop cleanly."""


class RecordingStorageCritical(RecordingWorkflowError):
    """Raised before capture when the meeting volume has critically low free space."""


class RecordingStopError(RecordingWorkflowError):
    """Raised after a capture shutdown failure has been persisted as interrupted."""


class CoordinatedCapture(Protocol):
    def start(self) -> CaptureManifest: ...

    def pause(self, *, timeout_seconds: float = 2.0) -> None: ...

    def resume(self) -> None: ...

    def stop(self, *, timeout_seconds: float = 5.0) -> CaptureManifest: ...


class CoordinatedCaptureFactory(Protocol):
    def __call__(
        self,
        *,
        session_id: str,
        session_directory: Path,
        configs: tuple[SourceCaptureConfig, SourceCaptureConfig],
        stream_factory: AudioStreamFactory,
        on_audio_level: Callable[[AudioLevelSnapshot], None],
    ) -> CoordinatedCapture: ...


class SourcePreflight(Protocol):
    def start(self) -> None: ...

    def stop(self, *, timeout_seconds: float = 2.0) -> None: ...


class SourcePreflightFactory(Protocol):
    def __call__(
        self,
        *,
        configs: tuple[SourceCaptureConfig, SourceCaptureConfig],
        stream_factory: AudioStreamFactory,
        on_audio_level: Callable[[AudioLevelSnapshot], None],
    ) -> SourcePreflight: ...


class StorageChecker(Protocol):
    def check(self) -> DiskSpaceStatus: ...


@dataclass(frozen=True, slots=True)
class RecordingLevels:
    microphone: float = 0.0
    system_audio: float = 0.0


class RecordingWorkflow(Protocol):
    @property
    def is_recording(self) -> bool: ...

    @property
    def is_preflighting(self) -> bool: ...

    def discover_devices(self) -> AudioDeviceCatalog: ...

    def storage_status(self) -> DiskSpaceStatus: ...

    def start_preflight(
        self,
        session_id: str,
        microphone_id: str,
        loopback_id: str,
        *,
        consent_confirmed: bool,
    ) -> None: ...

    def stop_preflight(self) -> None: ...

    def start(
        self,
        session_id: str,
        microphone_id: str,
        loopback_id: str,
        *,
        consent_confirmed: bool,
    ) -> MeetingSession: ...

    def stop(self) -> RecordingStopResult: ...

    def pause(self) -> MeetingSession: ...

    def resume(self) -> MeetingSession: ...

    def latest_levels(self) -> RecordingLevels: ...


def build_dual_source_capture(
    *,
    session_id: str,
    session_directory: Path,
    configs: tuple[SourceCaptureConfig, SourceCaptureConfig],
    stream_factory: AudioStreamFactory,
    on_audio_level: Callable[[AudioLevelSnapshot], None],
) -> DualSourceCapture:
    return DualSourceCapture(
        session_id,
        session_directory,
        configs,
        stream_factory,
        on_audio_level=on_audio_level,
    )


def build_source_preflight(
    *,
    configs: tuple[SourceCaptureConfig, SourceCaptureConfig],
    stream_factory: AudioStreamFactory,
    on_audio_level: Callable[[AudioLevelSnapshot], None],
) -> AudioSourcePreflight:
    return AudioSourcePreflight(configs, stream_factory, on_audio_level)


@dataclass(frozen=True, slots=True)
class RecordingStopResult:
    session: MeetingSession
    capture_manifest: CaptureManifest


@dataclass(slots=True)
class _ActiveRecording:
    session_id: str
    capture: CoordinatedCapture


class MeetingRecordingService:
    """Enforce consent, session state, endpoint selection, and capture ordering."""

    def __init__(
        self,
        session_service: MeetingSessionService,
        device_discovery: AudioDeviceDiscovery,
        stream_factory: AudioStreamFactory,
        capture_factory: CoordinatedCaptureFactory = build_dual_source_capture,
        preflight_factory: SourcePreflightFactory = build_source_preflight,
        storage_checker: StorageChecker | None = None,
    ):
        self.session_service = session_service
        self.device_discovery = device_discovery
        self.stream_factory = stream_factory
        self.capture_factory = capture_factory
        self.preflight_factory = preflight_factory
        self.storage_checker = storage_checker or DiskSpaceChecker(session_service.store.root)
        self._active: _ActiveRecording | None = None
        self._active_preflight: SourcePreflight | None = None
        self._level_lock = Lock()
        self._latest_levels = RecordingLevels()

    @property
    def is_recording(self) -> bool:
        return self._active is not None

    @property
    def is_preflighting(self) -> bool:
        return self._active_preflight is not None

    def discover_devices(self) -> AudioDeviceCatalog:
        return self.device_discovery.discover_devices()

    def storage_status(self) -> DiskSpaceStatus:
        return self.storage_checker.check()

    def latest_levels(self) -> RecordingLevels:
        with self._level_lock:
            return self._latest_levels

    def start_preflight(
        self,
        session_id: str,
        microphone_id: str,
        loopback_id: str,
        *,
        consent_confirmed: bool,
    ) -> None:
        if self._active is not None:
            raise RecordingWorkflowError("A meeting is already recording")
        if self._active_preflight is not None:
            raise RecordingWorkflowError("An audio source test is already running")
        self._require_consent(consent_confirmed)
        configs = self._selected_configs(microphone_id, loopback_id)

        try:
            self.session_service.confirm_recording_consent(session_id)
        except (OSError, ValueError) as error:
            raise RecordingWorkflowError("Meeting consent could not be persisted") from error

        with self._level_lock:
            self._latest_levels = RecordingLevels()
        preflight = self.preflight_factory(
            configs=configs,
            stream_factory=self.stream_factory,
            on_audio_level=self._record_audio_level,
        )
        try:
            preflight.start()
        except Exception as error:
            raise RecordingPreflightError("Audio source test could not start") from error
        self._active_preflight = preflight

    def stop_preflight(self) -> None:
        preflight = self._active_preflight
        if preflight is None:
            raise RecordingWorkflowError("No audio source test is running")
        self._active_preflight = None
        try:
            preflight.stop()
        except Exception as error:
            raise RecordingPreflightError("Audio source test could not stop cleanly") from error
        finally:
            with self._level_lock:
                self._latest_levels = RecordingLevels()

    def start(
        self,
        session_id: str,
        microphone_id: str,
        loopback_id: str,
        *,
        consent_confirmed: bool,
    ) -> MeetingSession:
        if self._active is not None:
            raise RecordingWorkflowError("Another meeting is already recording")
        if self._active_preflight is not None:
            raise RecordingWorkflowError("Stop the audio source test before recording")
        try:
            storage = self.storage_status()
        except OSError as error:
            raise RecordingWorkflowError("Meeting storage could not be checked") from error
        if storage.health is StorageHealth.CRITICAL:
            raise RecordingStorageCritical(storage.display_text)
        self._require_consent(consent_confirmed)
        configs = self._selected_configs(microphone_id, loopback_id)

        try:
            self.session_service.confirm_recording_consent(session_id)
            recording_session = self.session_service.transition_state(
                session_id,
                SessionState.RECORDING,
            )
        except (OSError, ValueError) as error:
            raise RecordingWorkflowError(
                "Meeting consent or recording state could not be persisted"
            ) from error
        try:
            with self._level_lock:
                self._latest_levels = RecordingLevels()
            capture = self.capture_factory(
                session_id=session_id,
                session_directory=self.session_service.store.session_directory(session_id),
                configs=configs,
                stream_factory=self.stream_factory,
                on_audio_level=self._record_audio_level,
            )
            capture.start()
        except Exception as error:
            self.session_service.transition_state(session_id, SessionState.INTERRUPTED)
            raise RecordingStartError("Audio capture could not start") from error

        self._active = _ActiveRecording(session_id, capture)
        return recording_session

    def stop(self) -> RecordingStopResult:
        active = self._active
        if active is None:
            raise RecordingWorkflowError("No meeting is currently recording")
        try:
            manifest = active.capture.stop()
        except Exception as error:
            self._active = None
            self.session_service.transition_state(active.session_id, SessionState.INTERRUPTED)
            raise RecordingStopError("Audio capture could not stop cleanly") from error

        self._active = None
        target = (
            SessionState.INTERRUPTED
            if manifest.state is CaptureJournalState.INTERRUPTED or manifest.errors
            else SessionState.RECORDED
        )
        try:
            session = self.session_service.transition_state(active.session_id, target)
        except (OSError, ValueError) as error:
            raise RecordingStopError(
                "Audio capture stopped, but the meeting state could not be persisted"
            ) from error
        return RecordingStopResult(session, manifest)

    @staticmethod
    def _require_consent(consent_confirmed: bool) -> None:
        if not consent_confirmed:
            raise RecordingConsentRequired(
                "Confirm participant notice and recording consent before opening audio sources"
            )

    def _selected_configs(
        self,
        microphone_id: str,
        loopback_id: str,
    ) -> tuple[SourceCaptureConfig, SourceCaptureConfig]:
        try:
            catalog = self.discover_devices()
        except DeviceDiscoveryError as error:
            raise RecordingDeviceUnavailable("Audio devices could not be refreshed") from error
        microphone = self._select_device(
            catalog.microphones,
            microphone_id,
            AudioDeviceKind.MICROPHONE,
        )
        loopback = self._select_device(
            catalog.loopbacks,
            loopback_id,
            AudioDeviceKind.SYSTEM_LOOPBACK,
        )
        return (
            self._config_for(microphone, maximum_channels=1),
            self._config_for(loopback, maximum_channels=2),
        )

    def pause(self) -> MeetingSession:
        active = self._active
        if active is None:
            raise RecordingWorkflowError("No meeting is currently recording")
        try:
            active.capture.pause()
        except Exception as error:
            raise RecordingWorkflowError("Audio capture could not pause cleanly") from error
        try:
            session = self.session_service.transition_state(active.session_id, SessionState.PAUSED)
        except (OSError, ValueError) as error:
            with suppress(Exception):
                active.capture.resume()
            raise RecordingWorkflowError("Paused meeting state could not be persisted") from error
        with self._level_lock:
            self._latest_levels = RecordingLevels()
        return session

    def resume(self) -> MeetingSession:
        active = self._active
        if active is None:
            raise RecordingWorkflowError("No meeting is currently paused")
        current = self.session_service.get_session(active.session_id)
        if current.state is not SessionState.PAUSED:
            raise RecordingWorkflowError("Meeting is not paused")
        try:
            active.capture.resume()
        except Exception as error:
            raise RecordingWorkflowError("Audio capture could not resume cleanly") from error
        try:
            return self.session_service.transition_state(active.session_id, SessionState.RECORDING)
        except (OSError, ValueError) as error:
            with suppress(Exception):
                active.capture.pause()
            raise RecordingWorkflowError("Resumed meeting state could not be persisted") from error

    @staticmethod
    def _select_device(
        devices: tuple[AudioDevice, ...],
        device_id: str,
        expected_kind: AudioDeviceKind,
    ) -> AudioDevice:
        device = next(
            (candidate for candidate in devices if candidate.device_id == device_id), None
        )
        if device is None or device.kind is not expected_kind:
            raise RecordingDeviceUnavailable(
                f"Selected {expected_kind.value} device is no longer available"
            )
        return device

    @staticmethod
    def _config_for(device: AudioDevice, *, maximum_channels: int) -> SourceCaptureConfig:
        audio_format = AudioFormat(
            sample_rate=device.default_sample_rate,
            channels=min(device.max_input_channels, maximum_channels),
        )
        return SourceCaptureConfig(device, audio_format)

    def _record_audio_level(self, snapshot: AudioLevelSnapshot) -> None:
        with self._level_lock:
            if snapshot.source is AudioDeviceKind.MICROPHONE:
                self._latest_levels = RecordingLevels(
                    microphone=snapshot.peak,
                    system_audio=self._latest_levels.system_audio,
                )
            else:
                self._latest_levels = RecordingLevels(
                    microphone=self._latest_levels.microphone,
                    system_audio=snapshot.peak,
                )

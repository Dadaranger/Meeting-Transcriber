from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.capture.devices import (
    AudioDevice,
    AudioDeviceCatalog,
    AudioDeviceDiscovery,
    AudioDeviceKind,
    DeviceDiscoveryError,
)
from meeting_transcriber.capture.formats import AudioFormat
from meeting_transcriber.capture.manifest import (
    CaptureJournalState,
    CaptureManifest,
)
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


class RecordingStopError(RecordingWorkflowError):
    """Raised after a capture shutdown failure has been persisted as interrupted."""


class CoordinatedCapture(Protocol):
    def start(self) -> CaptureManifest: ...

    def stop(self, *, timeout_seconds: float = 5.0) -> CaptureManifest: ...


class CoordinatedCaptureFactory(Protocol):
    def __call__(
        self,
        *,
        session_id: str,
        session_directory: Path,
        configs: tuple[SourceCaptureConfig, SourceCaptureConfig],
        stream_factory: AudioStreamFactory,
    ) -> CoordinatedCapture: ...


class RecordingWorkflow(Protocol):
    @property
    def is_recording(self) -> bool: ...

    def discover_devices(self) -> AudioDeviceCatalog: ...

    def start(
        self,
        session_id: str,
        microphone_id: str,
        loopback_id: str,
        *,
        consent_confirmed: bool,
    ) -> MeetingSession: ...

    def stop(self) -> RecordingStopResult: ...


def build_dual_source_capture(
    *,
    session_id: str,
    session_directory: Path,
    configs: tuple[SourceCaptureConfig, SourceCaptureConfig],
    stream_factory: AudioStreamFactory,
) -> DualSourceCapture:
    return DualSourceCapture(
        session_id,
        session_directory,
        configs,
        stream_factory,
    )


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
    ):
        self.session_service = session_service
        self.device_discovery = device_discovery
        self.stream_factory = stream_factory
        self.capture_factory = capture_factory
        self._active: _ActiveRecording | None = None

    @property
    def is_recording(self) -> bool:
        return self._active is not None

    def discover_devices(self) -> AudioDeviceCatalog:
        return self.device_discovery.discover_devices()

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
        if not consent_confirmed:
            raise RecordingConsentRequired(
                "Confirm participant notice and recording consent before recording"
            )

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
        configs = (
            self._config_for(microphone, maximum_channels=1),
            self._config_for(loopback, maximum_channels=2),
        )

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
            capture = self.capture_factory(
                session_id=session_id,
                session_directory=self.session_service.store.session_directory(session_id),
                configs=configs,
                stream_factory=self.stream_factory,
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

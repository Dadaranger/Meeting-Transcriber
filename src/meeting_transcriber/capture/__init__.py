"""Audio-device discovery and capture adapters."""

from meeting_transcriber.capture.chunks import AudioChunkMetadata, WavChunkWriter
from meeting_transcriber.capture.devices import (
    AudioDevice,
    AudioDeviceCatalog,
    AudioDeviceDiscovery,
    AudioDeviceKind,
    DeviceDiscoveryError,
    UnsupportedCapturePlatform,
)
from meeting_transcriber.capture.formats import AudioFormat
from meeting_transcriber.capture.manifest import (
    CaptureJournalState,
    CaptureManifest,
    CaptureSourceManifest,
)
from meeting_transcriber.capture.recorder import CaptureCoordinatorState, DualSourceCapture
from meeting_transcriber.capture.streams import AudioStreamError, SourceCaptureConfig

__all__ = [
    "AudioChunkMetadata",
    "AudioDevice",
    "AudioDeviceCatalog",
    "AudioDeviceDiscovery",
    "AudioDeviceKind",
    "AudioFormat",
    "AudioStreamError",
    "CaptureCoordinatorState",
    "CaptureJournalState",
    "CaptureManifest",
    "CaptureSourceManifest",
    "DeviceDiscoveryError",
    "DualSourceCapture",
    "SourceCaptureConfig",
    "UnsupportedCapturePlatform",
    "WavChunkWriter",
]

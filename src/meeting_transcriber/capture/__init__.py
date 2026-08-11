"""Audio-device discovery and capture adapters."""

from meeting_transcriber.capture.chunks import AudioChunkMetadata, WavChunkWriter
from meeting_transcriber.capture.devices import (
    AudioDevice,
    AudioDeviceCatalog,
    AudioDeviceKind,
    DeviceDiscoveryError,
    UnsupportedCapturePlatform,
)
from meeting_transcriber.capture.formats import AudioFormat

__all__ = [
    "AudioChunkMetadata",
    "AudioDevice",
    "AudioDeviceCatalog",
    "AudioDeviceKind",
    "AudioFormat",
    "DeviceDiscoveryError",
    "UnsupportedCapturePlatform",
    "WavChunkWriter",
]

"""Audio-device discovery and capture adapters."""

from meeting_transcriber.capture.devices import (
    AudioDevice,
    AudioDeviceCatalog,
    AudioDeviceKind,
    DeviceDiscoveryError,
    UnsupportedCapturePlatform,
)

__all__ = [
    "AudioDevice",
    "AudioDeviceCatalog",
    "AudioDeviceKind",
    "DeviceDiscoveryError",
    "UnsupportedCapturePlatform",
]

from __future__ import annotations

import sys
from dataclasses import dataclass

from meeting_transcriber.capture.devices import (
    AudioDeviceDiscovery,
    UnsupportedCapturePlatform,
)
from meeting_transcriber.capture.streams import AudioStreamFactory


@dataclass(frozen=True, slots=True)
class PlatformCaptureBackend:
    """The device catalog and stream factory selected for the current desktop OS."""

    devices: AudioDeviceDiscovery
    streams: AudioStreamFactory
    display_name: str


def create_platform_capture_backend(platform_name: str | None = None) -> PlatformCaptureBackend:
    """Create the native capture adapters without importing another OS's dependencies."""

    selected_platform = platform_name or sys.platform
    if selected_platform == "win32":
        from meeting_transcriber.capture.windows_pyaudio import (
            PyAudioWPatchDeviceBackend,
            PyAudioWPatchStreamFactory,
        )

        return PlatformCaptureBackend(
            PyAudioWPatchDeviceBackend(),
            PyAudioWPatchStreamFactory(),
            "Windows WASAPI",
        )
    if selected_platform == "darwin":
        from meeting_transcriber.capture.macos_coreaudio import (
            MacAudioDeviceBackend,
            MacAudioStreamFactory,
        )

        return PlatformCaptureBackend(
            MacAudioDeviceBackend(),
            MacAudioStreamFactory(),
            "macOS Core Audio and ScreenCaptureKit",
        )
    raise UnsupportedCapturePlatform(
        f"Audio capture is not available on platform {selected_platform!r}"
    )

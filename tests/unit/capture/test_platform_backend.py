from __future__ import annotations

import pytest

from meeting_transcriber.capture.devices import UnsupportedCapturePlatform
from meeting_transcriber.capture.macos_coreaudio import (
    MacAudioDeviceBackend,
    MacAudioStreamFactory,
)
from meeting_transcriber.capture.platform_backend import create_platform_capture_backend
from meeting_transcriber.capture.windows_pyaudio import (
    PyAudioWPatchDeviceBackend,
    PyAudioWPatchStreamFactory,
)


def test_selects_windows_native_capture_without_loading_devices() -> None:
    backend = create_platform_capture_backend("win32")

    assert isinstance(backend.devices, PyAudioWPatchDeviceBackend)
    assert isinstance(backend.streams, PyAudioWPatchStreamFactory)
    assert backend.display_name == "Windows WASAPI"


def test_selects_macos_native_capture_without_loading_devices() -> None:
    backend = create_platform_capture_backend("darwin")

    assert isinstance(backend.devices, MacAudioDeviceBackend)
    assert isinstance(backend.streams, MacAudioStreamFactory)
    assert backend.display_name == "macOS Core Audio and ScreenCaptureKit"


def test_rejects_an_unsupported_desktop_platform() -> None:
    with pytest.raises(UnsupportedCapturePlatform, match="linux"):
        create_platform_capture_backend("linux")

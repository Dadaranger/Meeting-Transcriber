from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from meeting_transcriber.capture.devices import AudioDevice
from meeting_transcriber.capture.formats import AudioFormat


class AudioStreamError(RuntimeError):
    """Raised when a platform capture stream cannot start, read, or stop."""


@dataclass(frozen=True, slots=True)
class SourceCaptureConfig:
    device: AudioDevice
    audio_format: AudioFormat
    frames_per_buffer: int = 1_024

    def __post_init__(self) -> None:
        if self.frames_per_buffer < 1:
            raise ValueError("Audio frames per buffer must be positive")
        if self.audio_format.channels > self.device.max_input_channels:
            raise ValueError("Capture format requests more channels than the device supports")


class AudioInputStream(Protocol):
    def start(self) -> None: ...

    def read(self, frame_count: int) -> bytes: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


class AudioStreamFactory(Protocol):
    def open_input(self, config: SourceCaptureConfig) -> AudioInputStream: ...

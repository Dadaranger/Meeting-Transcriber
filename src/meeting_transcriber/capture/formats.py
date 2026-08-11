from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AudioFormat:
    """Interleaved signed PCM format used for capture and WAV storage."""

    sample_rate: int
    channels: int
    sample_width_bytes: int = 2

    def __post_init__(self) -> None:
        if self.sample_rate < 1:
            raise ValueError("Audio sample rate must be positive")
        if self.channels < 1:
            raise ValueError("Audio channel count must be positive")
        if self.sample_width_bytes not in {1, 2, 3, 4}:
            raise ValueError("WAV sample width must be between one and four bytes")

    @property
    def bytes_per_frame(self) -> int:
        return self.channels * self.sample_width_bytes

    def duration_ns(self, frame_count: int) -> int:
        if frame_count < 0:
            raise ValueError("Audio frame count cannot be negative")
        return round(frame_count * 1_000_000_000 / self.sample_rate)

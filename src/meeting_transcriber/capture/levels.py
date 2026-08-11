from __future__ import annotations

import struct
from dataclasses import dataclass

from meeting_transcriber.capture.devices import AudioDeviceKind


@dataclass(frozen=True, slots=True)
class AudioLevelSnapshot:
    source: AudioDeviceKind
    peak: float
    observed_monotonic_ns: int

    def __post_init__(self) -> None:
        if not 0.0 <= self.peak <= 1.0:
            raise ValueError("Audio peak must be between zero and one")
        if self.observed_monotonic_ns < 0:
            raise ValueError("Audio level timestamp cannot be negative")


def pcm16_peak(pcm: bytes) -> float:
    """Return the normalized absolute peak for little-endian signed 16-bit PCM."""

    if len(pcm) % 2:
        raise ValueError("16-bit PCM must contain complete samples")
    peak = max((abs(sample) for (sample,) in struct.iter_unpack("<h", pcm)), default=0)
    return min(1.0, peak / 32_768)

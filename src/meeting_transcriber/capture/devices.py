from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AudioDeviceKind(StrEnum):
    MICROPHONE = "microphone"
    SYSTEM_LOOPBACK = "system_loopback"


class DeviceDiscoveryError(RuntimeError):
    """Raised when the operating-system audio catalog cannot be read."""


class UnsupportedCapturePlatform(DeviceDiscoveryError):
    """Raised when no audio backend exists for the current platform."""


@dataclass(frozen=True, slots=True)
class AudioDevice:
    """A capture-capable audio endpoint exposed by a platform backend."""

    device_id: str
    backend_index: int
    name: str
    kind: AudioDeviceKind
    host_api: str
    max_input_channels: int
    default_sample_rate: int
    is_default: bool = False

    def __post_init__(self) -> None:
        if not self.device_id:
            raise ValueError("Audio device ID cannot be blank")
        if not self.name.strip():
            raise ValueError("Audio device name cannot be blank")
        if self.max_input_channels < 1:
            raise ValueError("Capture devices require at least one input channel")
        if self.default_sample_rate < 1:
            raise ValueError("Audio device sample rate must be positive")


@dataclass(frozen=True, slots=True)
class AudioDeviceCatalog:
    microphones: tuple[AudioDevice, ...]
    loopbacks: tuple[AudioDevice, ...]

    @property
    def all_devices(self) -> tuple[AudioDevice, ...]:
        return self.microphones + self.loopbacks

    @property
    def default_microphone(self) -> AudioDevice | None:
        return next((device for device in self.microphones if device.is_default), None)

    @property
    def default_loopback(self) -> AudioDevice | None:
        return next((device for device in self.loopbacks if device.is_default), None)

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Mapping
from hashlib import sha256
from queue import Empty, Queue
from types import TracebackType
from typing import Protocol, cast

from meeting_transcriber.capture.devices import (
    AudioDevice,
    AudioDeviceCatalog,
    AudioDeviceKind,
    DeviceDiscoveryError,
    UnsupportedCapturePlatform,
)
from meeting_transcriber.capture.streams import (
    AudioStreamError,
    SourceCaptureConfig,
)


class _AudioManager(Protocol):
    def __enter__(self) -> _AudioManager: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def get_host_api_info_by_type(self, host_api_type: int) -> Mapping[str, object]: ...

    def get_device_info_generator(self) -> Iterator[Mapping[str, object]]: ...

    def get_default_wasapi_loopback(self) -> Mapping[str, object]: ...


class _PortAudioStream(Protocol):
    def start_stream(self) -> None: ...

    def stop_stream(self) -> None: ...

    def is_active(self) -> bool: ...

    def close(self) -> None: ...


class _StreamAudioManager(_AudioManager, Protocol):
    def open(
        self,
        *,
        format: int,
        channels: int,
        rate: int,
        input: bool,
        input_device_index: int,
        frames_per_buffer: int,
        start: bool,
        stream_callback: Callable[
            [bytes | None, int, Mapping[str, float], int],
            tuple[bytes | None, int],
        ],
    ) -> _PortAudioStream: ...

    def terminate(self) -> None: ...


class _PyAudioDiscoveryModule(Protocol):
    paWASAPI: int

    def PyAudio(self) -> _AudioManager: ...


class _PyAudioModule(_PyAudioDiscoveryModule, Protocol):
    paInt16: int
    paContinue: int


def _load_pyaudio_module() -> _PyAudioModule:
    if sys.platform != "win32":
        raise UnsupportedCapturePlatform("WASAPI capture is available only on Windows")
    try:
        import pyaudiowpatch  # type: ignore[import-untyped]
    except ImportError as error:
        raise DeviceDiscoveryError("PyAudioWPatch is not installed") from error
    return cast(_PyAudioModule, pyaudiowpatch)


class _ManagedPyAudioInputStream:
    def __init__(
        self,
        manager: _StreamAudioManager,
        stream: _PortAudioStream,
        audio_queue: Queue[bytes],
    ):
        self._manager = manager
        self._stream = stream
        self._audio_queue = audio_queue
        self._closed = False

    def start(self) -> None:
        try:
            self._stream.start_stream()
        except OSError as error:
            raise AudioStreamError("Could not start the Windows audio stream") from error

    def read(self, frame_count: int) -> bytes:
        del frame_count
        if self._closed:
            raise AudioStreamError("Cannot read from a closed Windows audio stream")
        try:
            return self._audio_queue.get(timeout=0.1)
        except Empty:
            return b""
        except (OSError, RuntimeError) as error:
            raise AudioStreamError("Could not read from the Windows audio stream") from error

    def stop(self) -> None:
        if self._closed:
            return
        try:
            if self._stream.is_active():
                self._stream.stop_stream()
        except OSError as error:
            raise AudioStreamError("Could not stop the Windows audio stream") from error

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._stream.close()
        finally:
            self._manager.terminate()
            self._closed = True


class PyAudioWPatchStreamFactory:
    """Open signed 16-bit PCM input streams for discovered WASAPI devices."""

    def __init__(self, module: _PyAudioModule | None = None):
        self._module = module

    def open_input(self, config: SourceCaptureConfig) -> _ManagedPyAudioInputStream:
        if config.audio_format.sample_width_bytes != 2:
            raise AudioStreamError("PyAudioWPatch capture currently requires 16-bit PCM")
        try:
            module = self._module or _load_pyaudio_module()
        except DeviceDiscoveryError as error:
            raise AudioStreamError(str(error)) from error

        manager = cast(_StreamAudioManager, module.PyAudio())
        audio_queue: Queue[bytes] = Queue()

        def capture_callback(
            in_data: bytes | None,
            _frame_count: int,
            _time_info: Mapping[str, float],
            _status_flags: int,
        ) -> tuple[bytes | None, int]:
            if in_data:
                audio_queue.put(bytes(in_data))
            return in_data, module.paContinue

        try:
            stream = manager.open(
                format=module.paInt16,
                channels=config.audio_format.channels,
                rate=config.audio_format.sample_rate,
                input=True,
                input_device_index=config.device.backend_index,
                frames_per_buffer=config.frames_per_buffer,
                start=False,
                stream_callback=capture_callback,
            )
        except (OSError, TypeError, ValueError) as error:
            manager.terminate()
            raise AudioStreamError(f"Could not open audio input: {config.device.name}") from error
        return _ManagedPyAudioInputStream(manager, stream, audio_queue)


def _required_int(info: Mapping[str, object], field: str) -> int:
    value = info.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DeviceDiscoveryError(f"Audio device field {field!r} is invalid")
    return int(value)


def _required_name(info: Mapping[str, object]) -> str:
    value = info.get("name")
    if not isinstance(value, str) or not value.strip():
        raise DeviceDiscoveryError("Audio device name is invalid")
    return value.strip()


def _stable_device_id(
    kind: AudioDeviceKind,
    name: str,
    channels: int,
    sample_rate: int,
) -> str:
    fingerprint = "\0".join((kind.value, name.casefold(), str(channels), str(sample_rate))).encode()
    digest = sha256(fingerprint).hexdigest()[:16]
    return f"wasapi:{kind.value}:{digest}"


class PyAudioWPatchDeviceBackend:
    """Discover Windows WASAPI inputs using PyAudioWPatch virtual loopback devices."""

    def __init__(self, module: _PyAudioDiscoveryModule | None = None):
        self._module = module

    def discover_devices(self) -> AudioDeviceCatalog:
        module = self._module or _load_pyaudio_module()
        try:
            with module.PyAudio() as manager:
                host_api = manager.get_host_api_info_by_type(module.paWASAPI)
                return self._catalog_from_manager(manager, host_api)
        except DeviceDiscoveryError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise DeviceDiscoveryError("Could not enumerate Windows WASAPI devices") from error

    def _catalog_from_manager(
        self,
        manager: _AudioManager,
        host_api: Mapping[str, object],
    ) -> AudioDeviceCatalog:
        host_api_index = _required_int(host_api, "index")
        host_api_name = str(host_api.get("name") or "Windows WASAPI")
        default_input_index = _required_int(host_api, "defaultInputDevice")
        default_loopback_index = self._default_loopback_index(manager)

        microphones: list[AudioDevice] = []
        loopbacks: list[AudioDevice] = []
        for info in manager.get_device_info_generator():
            if _required_int(info, "hostApi") != host_api_index:
                continue
            channels = _required_int(info, "maxInputChannels")
            if channels < 1:
                continue
            index = _required_int(info, "index")
            name = _required_name(info)
            sample_rate = _required_int(info, "defaultSampleRate")
            is_loopback = info.get("isLoopbackDevice") is True
            kind = AudioDeviceKind.SYSTEM_LOOPBACK if is_loopback else AudioDeviceKind.MICROPHONE
            device = AudioDevice(
                device_id=_stable_device_id(kind, name, channels, sample_rate),
                backend_index=index,
                name=name,
                kind=kind,
                host_api=host_api_name,
                max_input_channels=channels,
                default_sample_rate=sample_rate,
                is_default=(
                    index == default_loopback_index if is_loopback else index == default_input_index
                ),
            )
            (loopbacks if is_loopback else microphones).append(device)

        def sort_key(device: AudioDevice) -> tuple[bool, str]:
            return not device.is_default, device.name.casefold()

        return AudioDeviceCatalog(
            microphones=tuple(sorted(microphones, key=sort_key)),
            loopbacks=tuple(sorted(loopbacks, key=sort_key)),
        )

    @staticmethod
    def _default_loopback_index(manager: _AudioManager) -> int | None:
        try:
            return _required_int(manager.get_default_wasapi_loopback(), "index")
        except (DeviceDiscoveryError, OSError):
            return None

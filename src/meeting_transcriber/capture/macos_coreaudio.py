from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread
from typing import Protocol, cast

from meeting_transcriber.capture.devices import (
    AudioDevice,
    AudioDeviceCatalog,
    AudioDeviceKind,
    DeviceDiscoveryError,
    UnsupportedCapturePlatform,
)
from meeting_transcriber.capture.streams import AudioStreamError, SourceCaptureConfig

SYSTEM_AUDIO_DEVICE_ID = "screencapturekit:system-audio"
SYSTEM_AUDIO_HELPER_NAME = "MeetingTranscriberSystemAudio"


class _BytesValue(Protocol):
    def __bytes__(self) -> bytes: ...


class _CoreAudioInputStream(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


class _SoundDeviceDefaults(Protocol):
    device: object


class _SoundDeviceModule(Protocol):
    @property
    def default(self) -> _SoundDeviceDefaults: ...

    def query_devices(self) -> object: ...

    def query_hostapis(self, index: int) -> object: ...

    def RawInputStream(
        self,
        *,
        samplerate: int,
        blocksize: int,
        device: int,
        channels: int,
        dtype: str,
        callback: Callable[[object, int, object, object], None],
    ) -> _CoreAudioInputStream: ...


class _ReadablePipe(Protocol):
    def read(self, size: int = -1) -> bytes: ...

    def readline(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


class _Process(Protocol):
    @property
    def stdout(self) -> _ReadablePipe | None: ...

    @property
    def stderr(self) -> _ReadablePipe | None: ...

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


ProcessFactory = Callable[..., _Process]


def _running_on_macos() -> bool:
    return sys.platform == "darwin"


def _load_sounddevice() -> _SoundDeviceModule:
    if not _running_on_macos():
        raise UnsupportedCapturePlatform("Core Audio capture is available only on macOS")
    try:
        sounddevice = importlib.import_module("sounddevice")
    except ImportError as error:
        raise DeviceDiscoveryError("The macOS Core Audio runtime is not installed") from error
    return cast(_SoundDeviceModule, sounddevice)


def _required_number(info: Mapping[str, object], field: str) -> float:
    value = info.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DeviceDiscoveryError(f"Audio device field {field!r} is invalid")
    return float(value)


def _required_name(info: Mapping[str, object], field: str = "name") -> str:
    value = info.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DeviceDiscoveryError(f"Audio device field {field!r} is invalid")
    return value.strip()


def _default_input_index(module: _SoundDeviceModule) -> int | None:
    configured = module.default.device
    if isinstance(configured, Sequence) and not isinstance(configured, str | bytes):
        value = configured[0] if configured else None
    else:
        value = configured
    if isinstance(value, bool) or not isinstance(value, int | float) or int(value) < 0:
        return None
    return int(value)


def _stable_microphone_id(name: str, channels: int, sample_rate: int) -> str:
    fingerprint = "\0".join((name.casefold(), str(channels), str(sample_rate))).encode()
    return f"coreaudio:microphone:{sha256(fingerprint).hexdigest()[:16]}"


class MacAudioDeviceBackend:
    """Discover microphone inputs and the native ScreenCaptureKit system source."""

    def __init__(self, module: _SoundDeviceModule | None = None):
        self._module = module

    def discover_devices(self) -> AudioDeviceCatalog:
        module = self._module or _load_sounddevice()
        try:
            raw_devices = cast(Iterable[Mapping[str, object]], module.query_devices())
            default_input = _default_input_index(module)
            microphones: list[AudioDevice] = []
            for index, info in enumerate(raw_devices):
                channels = int(_required_number(info, "max_input_channels"))
                if channels < 1:
                    continue
                name = _required_name(info)
                sample_rate = round(_required_number(info, "default_samplerate"))
                host_api_index = int(_required_number(info, "hostapi"))
                host_api = cast(Mapping[str, object], module.query_hostapis(host_api_index))
                host_api_name = _required_name(host_api)
                microphones.append(
                    AudioDevice(
                        device_id=_stable_microphone_id(name, channels, sample_rate),
                        backend_index=index,
                        name=name,
                        kind=AudioDeviceKind.MICROPHONE,
                        host_api=host_api_name,
                        max_input_channels=channels,
                        default_sample_rate=sample_rate,
                        is_default=index == default_input,
                    )
                )
        except DeviceDiscoveryError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise DeviceDiscoveryError("Could not enumerate macOS Core Audio devices") from error

        loopback = AudioDevice(
            device_id=SYSTEM_AUDIO_DEVICE_ID,
            backend_index=-1,
            name="All meeting/system audio",
            kind=AudioDeviceKind.SYSTEM_LOOPBACK,
            host_api="ScreenCaptureKit",
            max_input_channels=2,
            default_sample_rate=48_000,
            is_default=True,
        )
        return AudioDeviceCatalog(tuple(microphones), (loopback,))


class _ManagedCoreAudioInputStream:
    def __init__(self, stream: _CoreAudioInputStream, audio_queue: Queue[bytes]):
        self._stream = stream
        self._audio_queue = audio_queue
        self._closed = False

    def start(self) -> None:
        try:
            self._stream.start()
        except (OSError, RuntimeError) as error:
            raise AudioStreamError(
                "Could not start the microphone. Allow Microphone access in System Settings > "
                "Privacy & Security, then try again."
            ) from error

    def read(self, frame_count: int) -> bytes:
        del frame_count
        if self._closed:
            raise AudioStreamError("Cannot read from a closed macOS microphone stream")
        try:
            return self._audio_queue.get(timeout=0.1)
        except Empty:
            return b""

    def stop(self) -> None:
        if self._closed:
            return
        try:
            self._stream.stop()
        except (OSError, RuntimeError) as error:
            raise AudioStreamError("Could not stop the macOS microphone stream") from error

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._stream.close()
        finally:
            self._closed = True


def mac_system_audio_helper_path() -> Path:
    """Return the first configured, bundled, or locally built helper location."""

    configured = os.environ.get("MEETING_TRANSCRIBER_MAC_AUDIO_HELPER", "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    bundle_root = getattr(sys, "_MEIPASS", None)
    if isinstance(bundle_root, str):
        candidates.append(Path(bundle_root) / SYSTEM_AUDIO_HELPER_NAME)
    executable = Path(sys.executable).resolve()
    candidates.append(executable.parent.parent / "Frameworks" / SYSTEM_AUDIO_HELPER_NAME)
    candidates.append(
        Path(__file__).resolve().parents[3] / "build" / "macos" / SYSTEM_AUDIO_HELPER_NAME
    )
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


class _ScreenCaptureKitInputStream:
    def __init__(
        self,
        config: SourceCaptureConfig,
        helper_path: Path,
        process_factory: ProcessFactory,
        *,
        startup_timeout_seconds: float = 15.0,
    ):
        self._config = config
        self._helper_path = helper_path
        self._process_factory = process_factory
        self._startup_timeout_seconds = startup_timeout_seconds
        self._process: _Process | None = None
        self._ready = Event()
        self._stderr_messages: list[str] = []
        self._stderr_thread: Thread | None = None
        self._closed = False

    def start(self) -> None:
        if self._closed:
            raise AudioStreamError("Cannot start a closed macOS system-audio stream")
        if self._process is not None:
            raise AudioStreamError("The macOS system-audio stream is already started")
        if not self._helper_path.is_file():
            raise AudioStreamError(
                "The bundled macOS system-audio helper is missing. Reinstall Meeting Transcriber."
            )
        try:
            process = self._process_factory(
                [str(self._helper_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as error:
            raise AudioStreamError("Could not launch macOS system-audio capture") from error
        self._process = process
        if process.stdout is None or process.stderr is None:
            self.close()
            raise AudioStreamError("macOS system-audio capture did not open its audio pipes")

        self._stderr_thread = Thread(
            target=self._read_stderr,
            args=(process.stderr,),
            name="macos-system-audio-status",
            daemon=True,
        )
        self._stderr_thread.start()
        deadline = time.monotonic() + self._startup_timeout_seconds
        while not self._ready.wait(0.05):
            if process.poll() is not None:
                message = self._failure_message()
                self.close()
                raise AudioStreamError(message)
            if time.monotonic() >= deadline:
                self.close()
                raise AudioStreamError(
                    "macOS did not authorize system-audio capture. Allow Screen & System Audio "
                    "Recording in System Settings > Privacy & Security, reopen Meeting "
                    "Transcriber, and try again."
                )

    def _read_stderr(self, pipe: _ReadablePipe) -> None:
        while True:
            line = pipe.readline()
            if not line:
                return
            message = line.decode("utf-8", errors="replace").strip()
            if message == "READY":
                self._ready.set()
            elif message:
                self._stderr_messages.append(message)

    def _failure_message(self) -> str:
        detail = self._stderr_messages[-1] if self._stderr_messages else "permission was denied"
        return (
            f"Could not capture macOS meeting/system audio: {detail}. Allow Screen & System "
            "Audio Recording in System Settings > Privacy & Security, reopen Meeting "
            "Transcriber, and try again."
        )

    def read(self, frame_count: int) -> bytes:
        process = self._process
        if process is None or process.stdout is None:
            raise AudioStreamError("The macOS system-audio stream is not running")
        bytes_requested = frame_count * self._config.audio_format.bytes_per_frame
        try:
            audio = process.stdout.read(bytes_requested)
        except OSError as error:
            raise AudioStreamError("Could not read macOS meeting/system audio") from error
        if audio:
            return audio
        if process.poll() is not None:
            raise AudioStreamError(self._failure_message())
        return b""

    def stop(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)

    def close(self) -> None:
        if self._closed:
            return
        process = self._process
        try:
            self.stop()
        finally:
            if process is not None:
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
            self._closed = True


class MacAudioStreamFactory:
    """Open signed PCM microphone and ScreenCaptureKit system-audio streams."""

    def __init__(
        self,
        module: _SoundDeviceModule | None = None,
        *,
        helper_path: Path | None = None,
        process_factory: ProcessFactory | None = None,
        startup_timeout_seconds: float = 15.0,
    ):
        self._module = module
        self._helper_path = helper_path
        self._process_factory = process_factory or cast(ProcessFactory, subprocess.Popen)
        self._startup_timeout_seconds = startup_timeout_seconds

    def open_input(
        self, config: SourceCaptureConfig
    ) -> _ManagedCoreAudioInputStream | _ScreenCaptureKitInputStream:
        if config.audio_format.sample_width_bytes != 2:
            raise AudioStreamError("macOS capture currently requires 16-bit PCM")
        if config.device.kind is AudioDeviceKind.SYSTEM_LOOPBACK:
            if config.audio_format.sample_rate != 48_000 or config.audio_format.channels != 2:
                raise AudioStreamError("macOS system audio requires 48 kHz stereo capture")
            return _ScreenCaptureKitInputStream(
                config,
                self._helper_path or mac_system_audio_helper_path(),
                self._process_factory,
                startup_timeout_seconds=self._startup_timeout_seconds,
            )

        module = self._module or _load_sounddevice()
        audio_queue: Queue[bytes] = Queue()

        def capture_callback(
            in_data: object,
            _frame_count: int,
            _time_info: object,
            _status: object,
        ) -> None:
            audio_queue.put(bytes(cast(_BytesValue, in_data)))

        try:
            stream = module.RawInputStream(
                samplerate=config.audio_format.sample_rate,
                blocksize=config.frames_per_buffer,
                device=config.device.backend_index,
                channels=config.audio_format.channels,
                dtype="int16",
                callback=capture_callback,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise AudioStreamError(f"Could not open microphone: {config.device.name}") from error
        return _ManagedCoreAudioInputStream(stream, audio_queue)

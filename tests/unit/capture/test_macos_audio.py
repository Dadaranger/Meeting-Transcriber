from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from meeting_transcriber.capture.devices import AudioDevice
from meeting_transcriber.capture.formats import AudioFormat
from meeting_transcriber.capture.macos_coreaudio import (
    SYSTEM_AUDIO_DEVICE_ID,
    MacAudioDeviceBackend,
    MacAudioStreamFactory,
)
from meeting_transcriber.capture.streams import AudioStreamError, SourceCaptureConfig


class FakeDefaults:
    device: object = (1, 0)


class FakeCoreAudioStream:
    def __init__(self, callback: Callable[[object, int, object, object], None]):
        self.callback = callback
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True
        self.callback(b"\x01\x00" * 256, 256, object(), object())

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True


class FakeSoundDeviceModule:
    default = FakeDefaults()

    def __init__(self) -> None:
        self.stream: FakeCoreAudioStream | None = None
        self.open_options: dict[str, object] = {}

    def query_devices(self) -> object:
        return [
            {
                "name": "Output only",
                "max_input_channels": 0,
                "default_samplerate": 48_000.0,
                "hostapi": 0,
            },
            {
                "name": "MacBook Microphone",
                "max_input_channels": 2,
                "default_samplerate": 48_000.0,
                "hostapi": 0,
            },
        ]

    def query_hostapis(self, index: int) -> object:
        assert index == 0
        return {"name": "Core Audio"}

    def RawInputStream(
        self,
        *,
        samplerate: int,
        blocksize: int,
        device: int,
        channels: int,
        dtype: str,
        callback: Callable[[object, int, object, object], None],
    ) -> FakeCoreAudioStream:
        self.open_options = {
            "samplerate": samplerate,
            "blocksize": blocksize,
            "device": device,
            "channels": channels,
            "dtype": dtype,
        }
        self.stream = FakeCoreAudioStream(callback)
        return self.stream


class FakePipe:
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if size >= 0 and len(chunk) > size:
            self.chunks.insert(0, chunk[size:])
            return chunk[:size]
        return chunk

    def readline(self, size: int = -1) -> bytes:
        return self.read(size)

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, audio: bytes):
        self.stdout = FakePipe([audio])
        self.stderr = FakePipe([b"READY\n"])
        self.exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.exit_code = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.exit_code = 0
        return 0

    def kill(self) -> None:
        self.exit_code = -9


def _microphone() -> AudioDevice:
    return MacAudioDeviceBackend(FakeSoundDeviceModule()).discover_devices().microphones[0]


def _system_audio() -> AudioDevice:
    return MacAudioDeviceBackend(FakeSoundDeviceModule()).discover_devices().loopbacks[0]


def test_discovers_core_audio_microphones_and_screencapturekit_system_audio() -> None:
    catalog = MacAudioDeviceBackend(FakeSoundDeviceModule()).discover_devices()

    assert [device.name for device in catalog.microphones] == ["MacBook Microphone"]
    assert catalog.default_microphone is not None
    assert catalog.default_microphone.backend_index == 1
    assert catalog.default_loopback is not None
    assert catalog.default_loopback.device_id == SYSTEM_AUDIO_DEVICE_ID
    assert catalog.default_loopback.host_api == "ScreenCaptureKit"
    assert catalog.default_loopback.default_sample_rate == 48_000


def test_opens_core_audio_microphone_as_pcm16() -> None:
    module = FakeSoundDeviceModule()
    config = SourceCaptureConfig(_microphone(), AudioFormat(48_000, 1), frames_per_buffer=256)

    stream = MacAudioStreamFactory(module).open_input(config)
    stream.start()
    pcm = stream.read(256)
    stream.stop()
    stream.close()

    assert pcm == b"\x01\x00" * 256
    assert module.open_options == {
        "samplerate": 48_000,
        "blocksize": 256,
        "device": 1,
        "channels": 1,
        "dtype": "int16",
    }
    assert module.stream is not None
    assert module.stream.closed


def test_launches_bundled_screencapturekit_helper_for_system_audio(tmp_path: Path) -> None:
    helper = tmp_path / "MeetingTranscriberSystemAudio"
    helper.write_bytes(b"test helper")
    audio = b"\x02\x00" * 128 * 2
    process = FakeProcess(audio)
    launched: list[list[str]] = []

    def process_factory(arguments: list[str], **options: object) -> FakeProcess:
        assert options["stdout"] == -1
        assert options["stderr"] == -1
        launched.append(arguments)
        return process

    config = SourceCaptureConfig(_system_audio(), AudioFormat(48_000, 2), frames_per_buffer=128)
    stream = MacAudioStreamFactory(
        FakeSoundDeviceModule(),
        helper_path=helper,
        process_factory=process_factory,
        startup_timeout_seconds=1.0,
    ).open_input(config)

    stream.start()
    assert stream.read(128) == audio
    stream.stop()
    stream.close()

    assert launched == [[str(helper)]]
    assert process.exit_code == 0
    assert process.stdout.closed
    assert process.stderr.closed


def test_missing_system_audio_helper_has_reinstall_guidance(tmp_path: Path) -> None:
    config = SourceCaptureConfig(_system_audio(), AudioFormat(48_000, 2))
    stream = MacAudioStreamFactory(
        FakeSoundDeviceModule(),
        helper_path=tmp_path / "missing-helper",
    ).open_input(config)

    with pytest.raises(AudioStreamError, match="Reinstall Meeting Transcriber"):
        stream.start()

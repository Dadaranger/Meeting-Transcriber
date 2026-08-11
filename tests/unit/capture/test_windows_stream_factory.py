from __future__ import annotations

from collections.abc import Iterator, Mapping
from types import TracebackType

from meeting_transcriber.capture.devices import AudioDevice, AudioDeviceKind
from meeting_transcriber.capture.formats import AudioFormat
from meeting_transcriber.capture.streams import SourceCaptureConfig
from meeting_transcriber.capture.windows_pyaudio import PyAudioWPatchStreamFactory


class FakePortAudioStream:
    def __init__(self) -> None:
        self.active = False
        self.closed = False

    def start_stream(self) -> None:
        self.active = True

    def read(self, frame_count: int, *, exception_on_overflow: bool) -> bytes:
        assert frame_count == 512
        assert exception_on_overflow is False
        return b"\x01\x00" * frame_count * 2

    def stop_stream(self) -> None:
        self.active = False

    def is_active(self) -> bool:
        return self.active

    def close(self) -> None:
        self.closed = True


class FakeStreamManager:
    def __init__(self) -> None:
        self.stream = FakePortAudioStream()
        self.open_options: dict[str, object] = {}
        self.terminated = False

    def __enter__(self) -> FakeStreamManager:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def get_host_api_info_by_type(self, host_api_type: int) -> Mapping[str, object]:
        return {}

    def get_device_info_generator(self) -> Iterator[Mapping[str, object]]:
        return iter(())

    def get_default_wasapi_loopback(self) -> Mapping[str, object]:
        return {}

    def open(self, **options: object) -> FakePortAudioStream:
        self.open_options = options
        return self.stream

    def terminate(self) -> None:
        self.terminated = True


class FakeStreamModule:
    paWASAPI = 13
    paInt16 = 8

    def __init__(self) -> None:
        self.manager = FakeStreamManager()

    def PyAudio(self) -> FakeStreamManager:
        return self.manager


def test_opens_and_owns_a_pyaudio_input_stream() -> None:
    module = FakeStreamModule()
    device = AudioDevice(
        device_id="wasapi:test",
        backend_index=7,
        name="Test loopback",
        kind=AudioDeviceKind.SYSTEM_LOOPBACK,
        host_api="Windows WASAPI",
        max_input_channels=2,
        default_sample_rate=48_000,
    )
    config = SourceCaptureConfig(
        device,
        AudioFormat(sample_rate=48_000, channels=2),
        frames_per_buffer=512,
    )

    stream = PyAudioWPatchStreamFactory(module).open_input(config)
    stream.start()
    pcm = stream.read(512)
    stream.stop()
    stream.close()

    assert len(pcm) == 512 * 2 * 2
    assert module.manager.open_options == {
        "format": 8,
        "channels": 2,
        "rate": 48_000,
        "input": True,
        "input_device_index": 7,
        "frames_per_buffer": 512,
        "start": False,
    }
    assert module.manager.stream.closed
    assert module.manager.terminated

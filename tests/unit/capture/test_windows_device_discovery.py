from __future__ import annotations

from collections.abc import Iterator, Mapping
from types import TracebackType

from meeting_transcriber.capture.devices import AudioDeviceKind
from meeting_transcriber.capture.windows_pyaudio import PyAudioWPatchDeviceBackend


class FakeAudioManager:
    def __init__(self, devices: list[dict[str, object]]):
        self.devices = devices

    def __enter__(self) -> FakeAudioManager:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def get_host_api_info_by_type(self, host_api_type: int) -> Mapping[str, object]:
        assert host_api_type == 13
        return {
            "index": 2,
            "name": "Windows WASAPI",
            "defaultInputDevice": 1,
            "defaultOutputDevice": 4,
        }

    def get_device_info_generator(self) -> Iterator[Mapping[str, object]]:
        yield from self.devices

    def get_default_wasapi_loopback(self) -> Mapping[str, object]:
        return {"index": 5}


class FakePyAudioModule:
    paWASAPI = 13
    paInt16 = 8

    def __init__(self, devices: list[dict[str, object]]):
        self.devices = devices

    def PyAudio(self) -> FakeAudioManager:
        return FakeAudioManager(self.devices)


def _device(
    index: int,
    name: str,
    *,
    channels: int,
    host_api: int = 2,
    loopback: bool = False,
) -> dict[str, object]:
    return {
        "index": index,
        "name": name,
        "hostApi": host_api,
        "maxInputChannels": channels,
        "maxOutputChannels": 2 if channels == 0 else 0,
        "defaultSampleRate": 48_000.0,
        "isLoopbackDevice": loopback,
    }


def test_discovers_wasapi_microphones_and_loopback_analogues() -> None:
    module = FakePyAudioModule(
        [
            _device(1, "Laptop microphone", channels=2),
            _device(2, "Webcam microphone", channels=1),
            _device(4, "Laptop speakers", channels=0),
            _device(5, "Laptop speakers [Loopback]", channels=2, loopback=True),
            _device(8, "Legacy microphone", channels=1, host_api=0),
        ]
    )

    catalog = PyAudioWPatchDeviceBackend(module).discover_devices()

    assert [device.name for device in catalog.microphones] == [
        "Laptop microphone",
        "Webcam microphone",
    ]
    assert [device.name for device in catalog.loopbacks] == ["Laptop speakers [Loopback]"]
    assert catalog.default_microphone is not None
    assert catalog.default_microphone.backend_index == 1
    assert catalog.default_loopback is not None
    assert catalog.default_loopback.backend_index == 5
    assert catalog.default_loopback.kind is AudioDeviceKind.SYSTEM_LOOPBACK


def test_device_fingerprint_does_not_depend_on_backend_index() -> None:
    first = FakePyAudioModule([_device(3, "USB microphone", channels=1)])
    second = FakePyAudioModule([_device(17, "USB microphone", channels=1)])

    first_device = PyAudioWPatchDeviceBackend(first).discover_devices().microphones[0]
    second_device = PyAudioWPatchDeviceBackend(second).discover_devices().microphones[0]

    assert first_device.device_id == second_device.device_id
    assert first_device.backend_index != second_device.backend_index


def test_output_endpoint_without_loopback_input_is_not_a_capture_device() -> None:
    module = FakePyAudioModule([_device(4, "Speakers", channels=0)])

    catalog = PyAudioWPatchDeviceBackend(module).discover_devices()

    assert catalog.all_devices == ()

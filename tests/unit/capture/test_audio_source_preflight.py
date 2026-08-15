from __future__ import annotations

import time
from threading import Event

from meeting_transcriber.capture.devices import AudioDevice, AudioDeviceKind
from meeting_transcriber.capture.formats import AudioFormat
from meeting_transcriber.capture.levels import AudioLevelSnapshot
from meeting_transcriber.capture.preflight import AudioSourcePreflight
from meeting_transcriber.capture.streams import AudioInputStream, SourceCaptureConfig


class FakeStream:
    def __init__(self, pcm: bytes):
        self.pcm = pcm
        self.started = False
        self.stopped = False
        self.closed = False
        self._first_read = True
        self._cancelled = Event()

    def start(self) -> None:
        self.started = True

    def read(self, frame_count: int) -> bytes:
        assert frame_count == 4
        if self._first_read:
            self._first_read = False
            return self.pcm
        self._cancelled.wait(0.01)
        return b""

    def stop(self) -> None:
        self.stopped = True
        self._cancelled.set()

    def close(self) -> None:
        self.closed = True


class FakeStreamFactory:
    def __init__(self) -> None:
        self.streams: list[FakeStream] = []

    def open_input(self, config: SourceCaptureConfig) -> AudioInputStream:
        del config
        stream = FakeStream(b"\xff\x7f" * 4)
        self.streams.append(stream)
        return stream


def _config(kind: AudioDeviceKind, device_id: str) -> SourceCaptureConfig:
    channels = 1 if kind is AudioDeviceKind.MICROPHONE else 2
    device = AudioDevice(
        device_id=device_id,
        backend_index=len(device_id),
        name=device_id,
        kind=kind,
        host_api="Test",
        max_input_channels=channels,
        default_sample_rate=48_000,
    )
    return SourceCaptureConfig(device, AudioFormat(48_000, channels), frames_per_buffer=4)


def test_preflight_reads_levels_and_closes_sources_without_output() -> None:
    factory = FakeStreamFactory()
    snapshots: list[AudioLevelSnapshot] = []
    preflight = AudioSourcePreflight(
        (
            _config(AudioDeviceKind.MICROPHONE, "mic"),
            _config(AudioDeviceKind.SYSTEM_LOOPBACK, "loopback"),
        ),
        factory,
        snapshots.append,
    )

    preflight.start()
    deadline = time.monotonic() + 1.0
    while len(snapshots) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    preflight.stop()

    assert {snapshot.source for snapshot in snapshots} == {
        AudioDeviceKind.MICROPHONE,
        AudioDeviceKind.SYSTEM_LOOPBACK,
    }
    assert all(snapshot.peak > 0.99 for snapshot in snapshots)
    assert all(stream.started and stream.stopped and stream.closed for stream in factory.streams)

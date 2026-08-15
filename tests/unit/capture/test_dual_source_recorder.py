from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Event
from typing import TypedDict, cast

import pytest

from meeting_transcriber.capture.devices import AudioDevice, AudioDeviceKind
from meeting_transcriber.capture.formats import AudioFormat
from meeting_transcriber.capture.manifest import CaptureJournalState
from meeting_transcriber.capture.recorder import CaptureCoordinatorState, DualSourceCapture
from meeting_transcriber.capture.streams import SourceCaptureConfig


class _CaptureSourceDocument(TypedDict):
    chunks: list[object]


class _CaptureDocument(TypedDict):
    state: str
    sources: list[_CaptureSourceDocument]


class FakeInputStream:
    def __init__(self, pcm: bytes, *, fail_after: int | None = None):
        self.pcm = pcm
        self.fail_after = fail_after
        self.read_count = 0
        self.started = False
        self.stopped = False
        self.closed = False
        self.start_count = 0
        self.stop_count = 0
        self.failure_event = Event()

    def start(self) -> None:
        self.started = True
        self.start_count += 1

    def read(self, frame_count: int) -> bytes:
        assert frame_count == 2
        time.sleep(0.002)
        if self.fail_after is not None and self.read_count >= self.fail_after:
            self.failure_event.set()
            raise OSError("Synthetic device loss")
        self.read_count += 1
        return self.pcm

    def stop(self) -> None:
        self.stopped = True
        self.stop_count += 1

    def close(self) -> None:
        self.closed = True


class FakeStreamFactory:
    def __init__(self, streams: dict[AudioDeviceKind, FakeInputStream]):
        self.streams = streams

    def open_input(self, config: SourceCaptureConfig) -> FakeInputStream:
        return self.streams[config.device.kind]


def _device(kind: AudioDeviceKind, index: int) -> AudioDevice:
    return AudioDevice(
        device_id=f"device-{kind.value}",
        backend_index=index,
        name=kind.value,
        kind=kind,
        host_api="Test",
        max_input_channels=1,
        default_sample_rate=8,
        is_default=True,
    )


def _configs() -> tuple[SourceCaptureConfig, SourceCaptureConfig]:
    audio_format = AudioFormat(sample_rate=8, channels=1)
    return (
        SourceCaptureConfig(
            _device(AudioDeviceKind.MICROPHONE, 1),
            audio_format,
            frames_per_buffer=2,
        ),
        SourceCaptureConfig(
            _device(AudioDeviceKind.SYSTEM_LOOPBACK, 2),
            audio_format,
            frames_per_buffer=2,
        ),
    )


def _wait_for(predicate: object, *, timeout_seconds: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for synthetic audio capture")


def _read_capture_document(path: Path) -> _CaptureDocument:
    for attempt in range(20):
        try:
            return cast(_CaptureDocument, json.loads(path.read_text(encoding="utf-8")))
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.005)
    raise AssertionError("Capture document retry loop ended unexpectedly")


def test_dual_capture_journals_chunks_before_normal_stop(tmp_path: Path) -> None:
    streams = {
        AudioDeviceKind.MICROPHONE: FakeInputStream(b"\x01\x00" * 2),
        AudioDeviceKind.SYSTEM_LOOPBACK: FakeInputStream(b"\x02\x00" * 2),
    }
    capture = DualSourceCapture(
        "session-1",
        tmp_path,
        _configs(),
        FakeStreamFactory(streams),
        chunk_duration_seconds=0.5,
    )

    started = capture.start()
    _wait_for(lambda: all(stream.read_count >= 2 for stream in streams.values()))

    def both_sources_are_journaled() -> bool:
        document = _read_capture_document(tmp_path / "capture.json")
        return all(source["chunks"] for source in document["sources"])

    _wait_for(both_sources_are_journaled)
    in_progress = _read_capture_document(tmp_path / "capture.json")
    stopped = capture.stop()

    assert started.state is CaptureJournalState.RECORDING
    assert started.started_monotonic_ns == min(
        source.started_monotonic_ns for source in started.sources
    )
    assert in_progress["state"] == "recording"
    assert all(source["chunks"] for source in in_progress["sources"])
    assert stopped.state is CaptureJournalState.STOPPED
    assert stopped.errors == ()
    assert all(source.chunks for source in stopped.sources)
    assert all(stream.started and stream.stopped and stream.closed for stream in streams.values())


def test_source_failure_stops_both_streams_and_marks_interruption(tmp_path: Path) -> None:
    microphone = FakeInputStream(b"\x01\x00" * 2, fail_after=1)
    loopback = FakeInputStream(b"\x02\x00" * 2)
    streams = {
        AudioDeviceKind.MICROPHONE: microphone,
        AudioDeviceKind.SYSTEM_LOOPBACK: loopback,
    }
    capture = DualSourceCapture(
        "session-2",
        tmp_path,
        _configs(),
        FakeStreamFactory(streams),
        chunk_duration_seconds=0.5,
    )

    capture.start()
    assert microphone.failure_event.wait(timeout=1)
    manifest = capture.stop()

    assert manifest.state is CaptureJournalState.INTERRUPTED
    assert any("Synthetic device loss" in error for error in manifest.errors)
    assert loopback.stopped and loopback.closed


def test_pause_quiesces_both_streams_and_resume_continues_capture(tmp_path: Path) -> None:
    streams = {
        AudioDeviceKind.MICROPHONE: FakeInputStream(b"\x01\x00" * 2),
        AudioDeviceKind.SYSTEM_LOOPBACK: FakeInputStream(b"\x02\x00" * 2),
    }
    capture = DualSourceCapture(
        "session-pause",
        tmp_path,
        _configs(),
        FakeStreamFactory(streams),
        chunk_duration_seconds=0.5,
    )
    capture.start()
    _wait_for(lambda: all(stream.read_count >= 2 for stream in streams.values()))

    capture.pause()
    paused_counts = {source: stream.read_count for source, stream in streams.items()}
    time.sleep(0.03)

    assert capture.state is CaptureCoordinatorState.PAUSED
    assert all(stream.stop_count == 1 for stream in streams.values())
    assert {source: stream.read_count for source, stream in streams.items()} == paused_counts

    capture.resume()
    _wait_for(
        lambda: all(stream.read_count > paused_counts[source] for source, stream in streams.items())
    )
    manifest = capture.stop()

    assert manifest.state is CaptureJournalState.STOPPED
    assert all(stream.start_count == 2 for stream in streams.values())


def test_dual_capture_requires_one_device_of_each_kind(tmp_path: Path) -> None:
    microphone_config = _configs()[0]

    with pytest.raises(ValueError, match="one microphone and one system loopback"):
        DualSourceCapture(
            "session-3",
            tmp_path,
            (microphone_config, microphone_config),
            FakeStreamFactory({}),
        )

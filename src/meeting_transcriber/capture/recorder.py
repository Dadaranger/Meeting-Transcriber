from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from threading import Event, Thread

from meeting_transcriber.capture.chunks import WavChunkWriter
from meeting_transcriber.capture.devices import AudioDeviceKind
from meeting_transcriber.capture.manifest import CaptureManifest, CaptureManifestJournal
from meeting_transcriber.capture.streams import (
    AudioInputStream,
    AudioStreamFactory,
    SourceCaptureConfig,
)


class CaptureCoordinatorState(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    STOPPED = "stopped"


class _SourceWorker(Thread):
    def __init__(
        self,
        config: SourceCaptureConfig,
        stream: AudioInputStream,
        writer: WavChunkWriter,
        stop_event: Event,
        started_ns: int,
        clock: Callable[[], int],
    ):
        super().__init__(name=f"capture-{config.device.kind.value}", daemon=True)
        self.config = config
        self.stream = stream
        self.writer = writer
        self.stop_event = stop_event
        self.started_ns = started_ns
        self.clock = clock
        self.frames_read = 0
        self.stopped_ns: int | None = None
        self.errors: list[str] = []

    def run(self) -> None:
        try:
            while not self.stop_event.is_set():
                pcm = self.stream.read(self.config.frames_per_buffer)
                if not pcm:
                    continue
                frame_start_ns = self.started_ns + self.config.audio_format.duration_ns(
                    self.frames_read
                )
                self.writer.write_frames(pcm, frame_start_ns=frame_start_ns)
                self.frames_read += len(pcm) // self.config.audio_format.bytes_per_frame
        except Exception as error:
            self._record_error(error)
            self.stop_event.set()
        finally:
            try:
                self.writer.close()
            except Exception as error:
                self._record_error(error)
            self.stopped_ns = self.clock()
            try:
                self.stream.stop()
            except Exception as error:
                self._record_error(error)
            finally:
                try:
                    self.stream.close()
                except Exception as error:
                    self._record_error(error)

    def _record_error(self, error: Exception) -> None:
        self.errors.append(f"{self.config.device.kind.value}: {type(error).__name__}: {error}")


class DualSourceCapture:
    """Coordinate microphone and system-loopback capture on separate worker threads."""

    def __init__(
        self,
        session_id: str,
        session_directory: Path,
        configs: tuple[SourceCaptureConfig, SourceCaptureConfig],
        stream_factory: AudioStreamFactory,
        *,
        chunk_duration_seconds: float = 30.0,
        clock: Callable[[], int] = time.monotonic_ns,
    ):
        kinds = {config.device.kind for config in configs}
        expected_kinds = {AudioDeviceKind.MICROPHONE, AudioDeviceKind.SYSTEM_LOOPBACK}
        if kinds != expected_kinds:
            raise ValueError("Dual capture requires one microphone and one system loopback")
        self.session_id = session_id
        self.session_directory = session_directory
        self.configs = configs
        self.stream_factory = stream_factory
        self.chunk_duration_seconds = chunk_duration_seconds
        self.clock = clock
        self.state = CaptureCoordinatorState.IDLE
        self._stop_event = Event()
        self._workers: dict[AudioDeviceKind, _SourceWorker] = {}
        self._journal: CaptureManifestJournal | None = None

    def start(self) -> CaptureManifest:
        if self.state is not CaptureCoordinatorState.IDLE:
            raise RuntimeError("Dual-source capture has already started")

        streams: dict[AudioDeviceKind, AudioInputStream] = {}
        source_started_ns: dict[AudioDeviceKind, int] = {}
        try:
            for config in self.configs:
                streams[config.device.kind] = self.stream_factory.open_input(config)
            for config in self.configs:
                stream = streams[config.device.kind]
                stream.start()
                source_started_ns[config.device.kind] = self.clock()
        except Exception:
            self._close_unstarted_workers(streams)
            raise

        journal = CaptureManifestJournal(
            self.session_directory / "capture.json",
            self.session_id,
            self.configs,
            source_started_ns,
        )
        self._journal = journal
        audio_directory = self.session_directory / "audio"
        for config in self.configs:
            writer = WavChunkWriter(
                audio_directory,
                config.device.kind,
                config.audio_format,
                chunk_duration_seconds=self.chunk_duration_seconds,
                on_chunk_finalized=journal.record_chunk,
            )
            worker = _SourceWorker(
                config,
                streams[config.device.kind],
                writer,
                self._stop_event,
                source_started_ns[config.device.kind],
                self.clock,
            )
            self._workers[config.device.kind] = worker
            worker.start()

        self.state = CaptureCoordinatorState.RECORDING
        return journal.snapshot()

    def stop(self, *, timeout_seconds: float = 5.0) -> CaptureManifest:
        if self.state is not CaptureCoordinatorState.RECORDING or self._journal is None:
            raise RuntimeError("Dual-source capture is not recording")
        if timeout_seconds <= 0:
            raise ValueError("Capture stop timeout must be positive")

        self._stop_event.set()
        errors: list[str] = []
        source_stopped_ns: dict[AudioDeviceKind, int] = {}
        for source, worker in self._workers.items():
            worker.join(timeout_seconds)
            if worker.is_alive():
                with suppress(Exception):
                    worker.stream.stop()
                worker.join(1.0)
            if worker.is_alive():
                errors.append(
                    f"{source.value}: capture worker did not stop after stream cancellation"
                )
            errors.extend(worker.errors)
            source_stopped_ns[source] = worker.stopped_ns or self.clock()

        manifest = self._journal.finish(source_stopped_ns, tuple(errors))
        self.state = CaptureCoordinatorState.STOPPED
        return manifest

    @staticmethod
    def _close_unstarted_workers(streams: dict[AudioDeviceKind, AudioInputStream]) -> None:
        for stream in streams.values():
            with suppress(Exception):  # best-effort startup rollback
                stream.stop()
            with suppress(Exception):  # best-effort startup rollback
                stream.close()

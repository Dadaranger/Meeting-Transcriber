from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from threading import Event, Thread

from meeting_transcriber.capture.levels import AudioLevelSnapshot, pcm16_peak
from meeting_transcriber.capture.streams import (
    AudioInputStream,
    AudioStreamError,
    AudioStreamFactory,
    SourceCaptureConfig,
)


class _PreflightWorker(Thread):
    def __init__(
        self,
        config: SourceCaptureConfig,
        stream: AudioInputStream,
        stop_event: Event,
        on_audio_level: Callable[[AudioLevelSnapshot], None],
    ):
        super().__init__(name=f"preflight-{config.device.kind.value}", daemon=True)
        self.config = config
        self.stream = stream
        self.stop_event = stop_event
        self.on_audio_level = on_audio_level
        self.errors: list[str] = []

    def run(self) -> None:
        try:
            while not self.stop_event.is_set():
                pcm = self.stream.read(self.config.frames_per_buffer)
                if not pcm:
                    continue
                self.on_audio_level(
                    AudioLevelSnapshot(
                        source=self.config.device.kind,
                        peak=pcm16_peak(pcm),
                        observed_monotonic_ns=time.monotonic_ns(),
                    )
                )
        except Exception as error:
            self.errors.append(f"{self.config.device.kind.value}: {type(error).__name__}: {error}")
            self.stop_event.set()
        finally:
            with suppress(Exception):
                self.stream.stop()
            with suppress(Exception):
                self.stream.close()


class AudioSourcePreflight:
    """Read both selected sources for level metering without persisting audio."""

    def __init__(
        self,
        configs: tuple[SourceCaptureConfig, SourceCaptureConfig],
        stream_factory: AudioStreamFactory,
        on_audio_level: Callable[[AudioLevelSnapshot], None],
    ):
        self.configs = configs
        self.stream_factory = stream_factory
        self.on_audio_level = on_audio_level
        self._stop_event = Event()
        self._workers: list[_PreflightWorker] = []
        self._started = False

    def start(self) -> None:
        if self._started:
            raise RuntimeError("Audio source preflight has already started")

        streams: list[tuple[SourceCaptureConfig, AudioInputStream]] = []
        try:
            for config in self.configs:
                streams.append((config, self.stream_factory.open_input(config)))
            for _config, stream in streams:
                stream.start()
        except Exception:
            self._close_streams(stream for _config, stream in streams)
            raise

        self._workers = [
            _PreflightWorker(config, stream, self._stop_event, self.on_audio_level)
            for config, stream in streams
        ]
        self._started = True
        for worker in self._workers:
            worker.start()

    def stop(self, *, timeout_seconds: float = 2.0) -> None:
        if not self._started:
            raise RuntimeError("Audio source preflight is not running")
        if timeout_seconds <= 0:
            raise ValueError("Preflight stop timeout must be positive")

        self._stop_event.set()
        errors: list[str] = []
        for worker in self._workers:
            worker.join(timeout_seconds)
            if worker.is_alive():
                with suppress(Exception):
                    worker.stream.stop()
                worker.join(1.0)
            if worker.is_alive():
                errors.append(f"{worker.config.device.kind.value}: source test worker did not stop")
            errors.extend(worker.errors)
        self._started = False
        if errors:
            raise AudioStreamError("; ".join(errors))

    @staticmethod
    def _close_streams(streams: Iterable[AudioInputStream]) -> None:
        for stream in streams:
            with suppress(Exception):
                stream.stop()
            with suppress(Exception):
                stream.close()

from __future__ import annotations

import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from meeting_transcriber.capture.devices import AudioDeviceKind
from meeting_transcriber.capture.formats import AudioFormat


@dataclass(frozen=True, slots=True)
class AudioChunkMetadata:
    sequence: int
    filename: str
    source: AudioDeviceKind
    start_monotonic_ns: int
    end_monotonic_ns: int
    frame_count: int
    byte_count: int
    sample_rate: int
    channels: int
    sample_width_bytes: int


class WavChunkWriter:
    """Write bounded PCM WAV chunks and finalize every completed file immediately."""

    def __init__(
        self,
        directory: Path,
        source: AudioDeviceKind,
        audio_format: AudioFormat,
        *,
        chunk_duration_seconds: float = 30.0,
        on_chunk_finalized: Callable[[AudioChunkMetadata], None] | None = None,
    ):
        if chunk_duration_seconds <= 0:
            raise ValueError("Audio chunk duration must be positive")
        self.directory = directory
        self.source = source
        self.audio_format = audio_format
        self.max_chunk_frames = max(1, round(audio_format.sample_rate * chunk_duration_seconds))
        self.on_chunk_finalized = on_chunk_finalized
        self._sequence = 0
        self._active_file: wave.Wave_write | None = None
        self._active_path: Path | None = None
        self._active_start_ns = 0
        self._active_frame_count = 0
        self._chunks: list[AudioChunkMetadata] = []
        self._closed = False

    @property
    def chunks(self) -> tuple[AudioChunkMetadata, ...]:
        return tuple(self._chunks)

    def write_frames(self, pcm: bytes, *, frame_start_ns: int) -> None:
        if self._closed:
            raise RuntimeError("Cannot write to a closed WAV chunk writer")
        if frame_start_ns < 0:
            raise ValueError("Monotonic frame timestamp cannot be negative")
        if len(pcm) % self.audio_format.bytes_per_frame != 0:
            raise ValueError("PCM byte count does not align to complete audio frames")

        total_frames = len(pcm) // self.audio_format.bytes_per_frame
        consumed_frames = 0
        while consumed_frames < total_frames:
            if self._active_file is None:
                chunk_start_ns = frame_start_ns + self.audio_format.duration_ns(consumed_frames)
                self._open_chunk(chunk_start_ns)

            available_frames = self.max_chunk_frames - self._active_frame_count
            write_frames = min(available_frames, total_frames - consumed_frames)
            byte_start = consumed_frames * self.audio_format.bytes_per_frame
            byte_end = byte_start + write_frames * self.audio_format.bytes_per_frame
            active_file = self._active_file
            if active_file is None:
                raise RuntimeError("WAV chunk was not opened")
            active_file.writeframesraw(pcm[byte_start:byte_end])
            self._active_frame_count += write_frames
            consumed_frames += write_frames

            if self._active_frame_count == self.max_chunk_frames:
                self._finalize_chunk()

    def close(self) -> tuple[AudioChunkMetadata, ...]:
        if self._closed:
            return self.chunks
        if self._active_file is not None:
            self._finalize_chunk()
        self._closed = True
        return self.chunks

    def __enter__(self) -> WavChunkWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _open_chunk(self, start_ns: int) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._sequence += 1
        path = self.directory / f"{self.source.value}_{self._sequence:04d}.wav"
        wav_file = wave.open(str(path), "wb")  # noqa: SIM115 - finalized on rotation/close
        wav_file.setnchannels(self.audio_format.channels)
        wav_file.setsampwidth(self.audio_format.sample_width_bytes)
        wav_file.setframerate(self.audio_format.sample_rate)
        self._active_file = wav_file
        self._active_path = path
        self._active_start_ns = start_ns
        self._active_frame_count = 0

    def _finalize_chunk(self) -> None:
        active_file = self._active_file
        active_path = self._active_path
        if active_file is None or active_path is None:
            raise RuntimeError("No active WAV chunk to finalize")

        active_file.close()
        frame_count = self._active_frame_count
        metadata = AudioChunkMetadata(
            sequence=self._sequence,
            filename=active_path.name,
            source=self.source,
            start_monotonic_ns=self._active_start_ns,
            end_monotonic_ns=(self._active_start_ns + self.audio_format.duration_ns(frame_count)),
            frame_count=frame_count,
            byte_count=frame_count * self.audio_format.bytes_per_frame,
            sample_rate=self.audio_format.sample_rate,
            channels=self.audio_format.channels,
            sample_width_bytes=self.audio_format.sample_width_bytes,
        )
        self._chunks.append(metadata)
        self._active_file = None
        self._active_path = None
        self._active_start_ns = 0
        self._active_frame_count = 0
        if self.on_chunk_finalized is not None:
            self.on_chunk_finalized(metadata)

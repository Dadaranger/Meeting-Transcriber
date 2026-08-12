from __future__ import annotations

import json
import os
import sys
import wave
from array import array
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

from meeting_transcriber.domain.transcript import TranscriptSource

TARGET_SAMPLE_RATE = 16_000
TARGET_CHANNELS = 1
TARGET_SAMPLE_WIDTH_BYTES = 2


class CapturePreparationError(ValueError):
    """Raised when finalized capture chunks cannot be safely prepared."""


@dataclass(frozen=True, slots=True)
class PreparedAudioChunk:
    source: TranscriptSource
    sequence: int
    path: Path
    timeline_start_ms: int
    duration_ms: int
    frame_count: int


@dataclass(frozen=True, slots=True)
class PreparedAudioPlan:
    session_id: str
    run_id: str
    chunks: tuple[PreparedAudioChunk, ...]
    total_audio_ms: int
    timeline_duration_ms: int

    def chunks_for(self, source: TranscriptSource) -> tuple[PreparedAudioChunk, ...]:
        return tuple(chunk for chunk in self.chunks if chunk.source is source)


@dataclass(frozen=True, slots=True)
class _WaveHeader:
    frame_count: int
    sample_rate: int
    channels: int
    sample_width_bytes: int


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CapturePreparationError(f"{field} must be a JSON object")
    return cast(Mapping[str, object], value)


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise CapturePreparationError(f"{field} must be a JSON array")
    return cast(list[object], value)


def _string(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise CapturePreparationError(f"{field} must be a non-empty string")
    return value


def _integer(document: Mapping[str, object], field: str) -> int:
    value = document.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CapturePreparationError(f"{field} must be an integer")
    return value


def _read_wave_header(path: Path) -> _WaveHeader:
    try:
        with wave.open(str(path), "rb") as wav_file:
            return _WaveHeader(
                frame_count=wav_file.getnframes(),
                sample_rate=wav_file.getframerate(),
                channels=wav_file.getnchannels(),
                sample_width_bytes=wav_file.getsampwidth(),
            )
    except (OSError, EOFError, wave.Error) as error:
        raise CapturePreparationError(
            f"Audio chunk is missing or unreadable: {path.name}"
        ) from error


def _expected_output_frames(frame_count: int, sample_rate: int) -> int:
    if sample_rate >= TARGET_SAMPLE_RATE:
        return (frame_count * TARGET_SAMPLE_RATE) // sample_rate
    return round(frame_count * TARGET_SAMPLE_RATE / sample_rate)


def _downmix(samples: array[int], channels: int) -> array[int]:
    if channels == 1:
        return samples
    mono = array("h")
    complete_values = len(samples) - (len(samples) % channels)
    for frame_start in range(0, complete_values, channels):
        total = 0
        for channel in range(channels):
            total += samples[frame_start + channel]
        mono.append(round(total / channels))
    return mono


def _resample(samples: array[int], source_rate: int) -> array[int]:
    if source_rate == TARGET_SAMPLE_RATE:
        return samples
    output_frames = _expected_output_frames(len(samples), source_rate)
    output = array("h")
    if source_rate > TARGET_SAMPLE_RATE:
        for output_index in range(output_frames):
            start = output_index * source_rate
            end = (output_index + 1) * source_rate
            first_source = start // TARGET_SAMPLE_RATE
            last_source = (end - 1) // TARGET_SAMPLE_RATE
            weighted_total = 0
            for source_index in range(first_source, last_source + 1):
                sample_start = source_index * TARGET_SAMPLE_RATE
                sample_end = (source_index + 1) * TARGET_SAMPLE_RATE
                overlap = min(end, sample_end) - max(start, sample_start)
                weighted_total += samples[source_index] * overlap
            output.append(round(weighted_total / source_rate))
        return output

    for output_index in range(output_frames):
        position = output_index * source_rate
        left_index = min(position // TARGET_SAMPLE_RATE, len(samples) - 1)
        right_index = min(left_index + 1, len(samples) - 1)
        fraction = position % TARGET_SAMPLE_RATE
        interpolated = (
            samples[left_index] * (TARGET_SAMPLE_RATE - fraction) + samples[right_index] * fraction
        ) / TARGET_SAMPLE_RATE
        output.append(round(interpolated))
    return output


def _normalize_wave(source: Path, destination: Path, header: _WaveHeader) -> int:
    expected_frames = _expected_output_frames(header.frame_count, header.sample_rate)
    if destination.is_file():
        existing = _read_wave_header(destination)
        if existing == _WaveHeader(
            expected_frames,
            TARGET_SAMPLE_RATE,
            TARGET_CHANNELS,
            TARGET_SAMPLE_WIDTH_BYTES,
        ):
            return expected_frames

    try:
        with wave.open(str(source), "rb") as wav_file:
            pcm = wav_file.readframes(header.frame_count)
    except (OSError, EOFError, wave.Error) as error:
        raise CapturePreparationError(f"Could not read audio chunk: {source.name}") from error
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    mono = _downmix(samples, header.channels)
    normalized = _resample(mono, header.sample_rate)
    if len(normalized) != expected_frames:
        raise CapturePreparationError(f"Normalized frame count is invalid for {source.name}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with wave.open(str(temporary), "wb") as wav_file:
            wav_file.setnchannels(TARGET_CHANNELS)
            wav_file.setsampwidth(TARGET_SAMPLE_WIDTH_BYTES)
            wav_file.setframerate(TARGET_SAMPLE_RATE)
            if sys.byteorder != "little":
                normalized.byteswap()
            wav_file.writeframes(normalized.tobytes())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return expected_frames


class AudioPreparationService:
    """Validate immutable capture chunks and create resumable 16 kHz mono copies."""

    def prepare(self, session_directory: Path, run_id: str) -> PreparedAudioPlan:
        normalized_run_id = str(UUID(run_id))
        manifest_path = session_directory / "capture.json"
        try:
            raw_document: object = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CapturePreparationError("Capture manifest could not be read") from error
        document = _mapping(raw_document, "capture manifest")
        session_id = _string(document, "session_id")
        UUID(session_id)
        capture_started_ns = _integer(document, "started_monotonic_ns")
        if capture_started_ns < 0:
            raise CapturePreparationError("Capture start timestamp cannot be negative")

        prepared: list[PreparedAudioChunk] = []
        output_directory = (
            session_directory / "derived" / "transcription" / normalized_run_id / "audio"
        )
        source_names: set[str] = set()
        for raw_source in _list(document.get("sources"), "sources"):
            source_document = _mapping(raw_source, "sources[]")
            capture_source = _string(source_document, "source")
            source_names.add(capture_source)
            try:
                source = {
                    "microphone": TranscriptSource.MICROPHONE,
                    "system_loopback": TranscriptSource.SYSTEM_AUDIO,
                }[capture_source]
            except KeyError as error:
                raise CapturePreparationError(
                    f"Unsupported capture source: {capture_source}"
                ) from error
            raw_chunks = _list(source_document.get("chunks"), f"{capture_source}.chunks")
            sequences = [_integer(_mapping(chunk, "chunks[]"), "sequence") for chunk in raw_chunks]
            if sequences != list(range(1, len(raw_chunks) + 1)):
                raise CapturePreparationError(f"{capture_source} chunk sequence is incomplete")
            for raw_chunk in raw_chunks:
                chunk = _mapping(raw_chunk, "chunks[]")
                prepared.append(
                    self._prepare_chunk(
                        session_directory,
                        output_directory,
                        source,
                        capture_started_ns,
                        chunk,
                    )
                )
        if source_names != {"microphone", "system_loopback"}:
            raise CapturePreparationError("Capture must contain microphone and system audio")
        if not prepared:
            raise CapturePreparationError("Capture has no finalized audio chunks")
        ordered = tuple(
            sorted(
                prepared, key=lambda chunk: (chunk.timeline_start_ms, chunk.source, chunk.sequence)
            )
        )
        return PreparedAudioPlan(
            session_id=session_id,
            run_id=normalized_run_id,
            chunks=ordered,
            total_audio_ms=sum(chunk.duration_ms for chunk in ordered),
            timeline_duration_ms=max(
                chunk.timeline_start_ms + chunk.duration_ms for chunk in ordered
            ),
        )

    @staticmethod
    def _prepare_chunk(
        session_directory: Path,
        output_directory: Path,
        source: TranscriptSource,
        capture_started_ns: int,
        chunk: Mapping[str, object],
    ) -> PreparedAudioChunk:
        filename = _string(chunk, "filename")
        if Path(filename).name != filename:
            raise CapturePreparationError("Capture chunk filename must not contain a path")
        sequence = _integer(chunk, "sequence")
        start_ns = _integer(chunk, "start_monotonic_ns")
        end_ns = _integer(chunk, "end_monotonic_ns")
        frame_count = _integer(chunk, "frame_count")
        sample_rate = _integer(chunk, "sample_rate")
        channels = _integer(chunk, "channels")
        sample_width = _integer(chunk, "sample_width_bytes")
        expected_header = _WaveHeader(frame_count, sample_rate, channels, sample_width)
        source_path = session_directory / "audio" / filename
        if _read_wave_header(source_path) != expected_header:
            raise CapturePreparationError(f"Audio chunk header does not match manifest: {filename}")
        if sample_width != TARGET_SAMPLE_WIDTH_BYTES:
            raise CapturePreparationError("Transcription preparation requires signed 16-bit PCM")
        if start_ns < capture_started_ns or end_ns <= start_ns:
            raise CapturePreparationError(f"Audio chunk timeline is invalid: {filename}")
        destination = output_directory / filename
        normalized_frames = _normalize_wave(source_path, destination, expected_header)
        return PreparedAudioChunk(
            source=source,
            sequence=sequence,
            path=destination,
            timeline_start_ms=(start_ns - capture_started_ns) // 1_000_000,
            duration_ms=round((end_ns - start_ns) / 1_000_000),
            frame_count=normalized_frames,
        )

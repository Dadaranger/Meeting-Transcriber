from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from meeting_transcriber.domain.transcript import TranscriptSource
from meeting_transcriber.processing.preparation import (
    AudioPreparationService,
    CapturePreparationError,
)

SESSION_ID = "0cc6ef84-1898-4214-81c8-2588f75ec7a9"
RUN_ID = "2e28f152-522a-4399-8313-d6d645703991"


def _write_wav(
    path: Path,
    *,
    channels: int,
    frames: int,
    samples: tuple[int, ...],
    sample_rate: int = 48_000,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = b"".join(
        sample.to_bytes(2, "little", signed=True) for _frame in range(frames) for sample in samples
    )
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)


def _chunk(
    source: str,
    *,
    channels: int,
    start_ns: int,
    sequence: int = 1,
    frames: int = 480,
) -> dict[str, object]:
    duration_ns = (frames * 1_000_000_000) // 48_000
    return {
        "sequence": sequence,
        "filename": f"{source}_{sequence:04d}.wav",
        "source": source,
        "start_monotonic_ns": start_ns,
        "end_monotonic_ns": start_ns + duration_ns,
        "frame_count": frames,
        "byte_count": frames * channels * 2,
        "sample_rate": 48_000,
        "channels": channels,
        "sample_width_bytes": 2,
    }


def _session_directory(tmp_path: Path) -> Path:
    directory = tmp_path / SESSION_ID
    microphone = _chunk("microphone", channels=1, start_ns=1_000_000_000)
    system = _chunk("system_loopback", channels=2, start_ns=1_010_000_000)
    (directory / "audio").mkdir(parents=True)
    _write_wav(
        directory / "audio" / str(microphone["filename"]),
        channels=1,
        frames=480,
        samples=(2_000,),
    )
    _write_wav(
        directory / "audio" / str(system["filename"]),
        channels=2,
        frames=480,
        samples=(1_000, -1_000),
    )
    (directory / "capture.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": SESSION_ID,
                "state": "stopped",
                "started_monotonic_ns": 1_000_000_000,
                "sources": [
                    {"source": "microphone", "chunks": [microphone]},
                    {"source": "system_loopback", "chunks": [system]},
                ],
            }
        ),
        encoding="utf-8",
    )
    return directory


def test_preparation_normalizes_both_sources_without_changing_originals(tmp_path: Path) -> None:
    directory = _session_directory(tmp_path)
    microphone_source = directory / "audio" / "microphone_0001.wav"
    original_bytes = microphone_source.read_bytes()

    plan = AudioPreparationService().prepare(directory, RUN_ID)

    assert plan.session_id == SESSION_ID
    assert plan.total_audio_ms == 20
    assert plan.timeline_duration_ms == 20
    assert [chunk.source for chunk in plan.chunks] == [
        TranscriptSource.MICROPHONE,
        TranscriptSource.SYSTEM_AUDIO,
    ]
    assert [chunk.timeline_start_ms for chunk in plan.chunks] == [0, 10]
    assert all(chunk.frame_count == 160 for chunk in plan.chunks)
    assert microphone_source.read_bytes() == original_bytes
    for chunk in plan.chunks:
        with wave.open(str(chunk.path), "rb") as wav_file:
            assert wav_file.getframerate() == 16_000
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getnframes() == 160
    with wave.open(str(plan.chunks[1].path), "rb") as system_wav:
        assert set(system_wav.readframes(160)) == {0}


def test_preparation_reuses_valid_completed_derived_chunks(tmp_path: Path) -> None:
    directory = _session_directory(tmp_path)
    service = AudioPreparationService()
    first = service.prepare(directory, RUN_ID)
    output = first.chunks[0].path
    first_modified_ns = output.stat().st_mtime_ns

    second = service.prepare(directory, RUN_ID)

    assert second == first
    assert output.stat().st_mtime_ns == first_modified_ns
    assert list(output.parent.glob("*.tmp")) == []


def test_preparation_rejects_wav_header_mismatch(tmp_path: Path) -> None:
    directory = _session_directory(tmp_path)
    _write_wav(
        directory / "audio" / "microphone_0001.wav",
        channels=1,
        frames=479,
        samples=(1,),
    )

    with pytest.raises(CapturePreparationError, match="header does not match"):
        AudioPreparationService().prepare(directory, RUN_ID)

from __future__ import annotations

import json
import wave
from pathlib import Path

from pytest import CaptureFixture

from meeting_transcriber.capture.audit import audit_capture_manifest, main


def _source(
    source: str,
    *,
    start_ns: int,
    chunk_count: int = 120,
    chunk_seconds: int = 30,
    final_extra_frames: int = 0,
) -> dict[str, object]:
    sample_rate = 1_000
    chunks: list[dict[str, object]] = []
    chunk_start_ns = start_ns
    for index in range(1, chunk_count + 1):
        frame_count = sample_rate * chunk_seconds
        if index == chunk_count:
            frame_count += final_extra_frames
        chunk_end_ns = chunk_start_ns + (frame_count * 1_000_000_000) // sample_rate
        chunks.append(
            {
                "sequence": index,
                "filename": f"{source}_{index:04d}.wav",
                "source": source,
                "start_monotonic_ns": chunk_start_ns,
                "end_monotonic_ns": chunk_end_ns,
                "frame_count": frame_count,
                "byte_count": frame_count * 2,
                "sample_rate": sample_rate,
                "channels": 1,
                "sample_width_bytes": 2,
            }
        )
        chunk_start_ns = chunk_end_ns
    return {"source": source, "chunks": chunks}


def _write_manifest(path: Path, sources: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": "synthetic-soak",
                "state": "stopped",
                "sources": sources,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_simulated_sixty_minute_soak_meets_alignment_target(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / "capture.json",
        [
            _source("microphone", start_ns=1_000_000_000),
            _source("system_loopback", start_ns=1_005_000_000),
        ],
    )

    report = audit_capture_manifest(
        manifest,
        minimum_duration_minutes=60,
        maximum_drift_ms=250,
        maximum_gap_ms=0,
        verify_audio=False,
    )

    assert report.passed
    assert report.end_alignment_drift_ms == 5.0
    assert [source.duration_seconds for source in report.sources] == [3_600.0, 3_600.0]
    assert all(source.chunk_count == 120 for source in report.sources)


def test_audit_fails_when_one_hour_source_drift_exceeds_target(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path / "capture.json",
        [
            _source("microphone", start_ns=1_000_000_000),
            _source(
                "system_loopback",
                start_ns=1_000_000_000,
                final_extra_frames=300,
            ),
        ],
    )

    report = audit_capture_manifest(
        manifest,
        minimum_duration_minutes=60,
        maximum_drift_ms=250,
        maximum_gap_ms=0,
        verify_audio=False,
    )

    assert not report.passed
    assert report.end_alignment_drift_ms == 300.0
    assert any("exceeds 250.000 ms" in issue for issue in report.issues)


def test_audit_reports_sequence_gaps_and_timeline_overlap(tmp_path: Path) -> None:
    microphone = _source("microphone", start_ns=0, chunk_count=2, chunk_seconds=1)
    system = _source("system_loopback", start_ns=0, chunk_count=2, chunk_seconds=1)
    microphone_chunks = microphone["chunks"]
    assert isinstance(microphone_chunks, list)
    second = microphone_chunks[1]
    assert isinstance(second, dict)
    second["sequence"] = 3
    second["start_monotonic_ns"] = 900_000_000
    second["end_monotonic_ns"] = 1_900_000_000
    manifest = _write_manifest(tmp_path / "capture.json", [microphone, system])

    report = audit_capture_manifest(manifest, verify_audio=False)

    assert not report.passed
    assert any("chunk sequence" in issue for issue in report.issues)
    assert any("overlaps" in issue for issue in report.issues)


def test_audit_verifies_wav_headers_against_manifest(tmp_path: Path) -> None:
    microphone = _source("microphone", start_ns=0, chunk_count=1, chunk_seconds=1)
    system = _source("system_loopback", start_ns=0, chunk_count=1, chunk_seconds=1)
    manifest = _write_manifest(tmp_path / "capture.json", [microphone, system])
    audio_directory = tmp_path / "audio"
    audio_directory.mkdir()
    for filename, frames in (
        ("microphone_0001.wav", 999),
        ("system_loopback_0001.wav", 1_000),
    ):
        with wave.open(str(audio_directory / filename), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(1_000)
            wav_file.writeframes(b"\x00\x00" * frames)

    report = audit_capture_manifest(manifest)

    assert not report.passed
    assert any("WAV header" in issue for issue in report.issues)


def test_audit_cli_returns_machine_readable_success(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    manifest = _write_manifest(
        tmp_path / "capture.json",
        [
            _source("microphone", start_ns=0, chunk_count=1),
            _source("system_loopback", start_ns=0, chunk_count=1),
        ],
    )

    exit_code = main([str(manifest), "--skip-audio-files", "--json"])

    assert exit_code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["passed"] is True

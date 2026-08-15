from __future__ import annotations

import argparse
import json
import sys
import wave
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

DEFAULT_MAX_DRIFT_MS = 250.0
TIMELINE_TOLERANCE_NS = 1_000


class CaptureAuditError(ValueError):
    """Raised when a capture manifest cannot be parsed for validation."""


@dataclass(frozen=True, slots=True)
class SourceAudit:
    source: str
    chunk_count: int
    frame_count: int
    duration_seconds: float
    first_start_monotonic_ns: int | None
    final_end_monotonic_ns: int | None
    max_gap_ms: float
    max_overlap_ms: float


@dataclass(frozen=True, slots=True)
class CaptureAuditReport:
    session_id: str
    sources: tuple[SourceAudit, ...]
    start_alignment_ms: float
    end_alignment_drift_ms: float
    max_allowed_drift_ms: float
    minimum_duration_minutes: float
    issues: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.issues


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CaptureAuditError(f"{field} must be a JSON object")
    return cast(Mapping[str, object], value)


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise CaptureAuditError(f"{field} must be a JSON array")
    return cast(list[object], value)


def _string(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise CaptureAuditError(f"{field} must be a non-empty string")
    return value


def _integer(document: Mapping[str, object], field: str) -> int:
    value = document.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CaptureAuditError(f"{field} must be an integer")
    return value


def _manifest_path(path: Path) -> Path:
    return path / "capture.json" if path.is_dir() else path


def _verify_wav(
    path: Path,
    *,
    expected_frames: int,
    expected_rate: int,
    expected_channels: int,
    expected_sample_width: int,
) -> str | None:
    try:
        with wave.open(str(path), "rb") as wav_file:
            actual = (
                wav_file.getnframes(),
                wav_file.getframerate(),
                wav_file.getnchannels(),
                wav_file.getsampwidth(),
            )
    except (OSError, EOFError, wave.Error) as error:
        return f"{path.name}: WAV is missing or unreadable ({error})"
    expected = (expected_frames, expected_rate, expected_channels, expected_sample_width)
    if actual != expected:
        return f"{path.name}: WAV header {actual} does not match manifest {expected}"
    return None


def _audit_source(
    source_document: Mapping[str, object],
    audio_directory: Path,
    *,
    minimum_duration_minutes: float,
    maximum_gap_ms: float | None,
    verify_audio: bool,
) -> tuple[SourceAudit, list[str]]:
    source = _string(source_document, "source")
    raw_chunks = _sequence(source_document.get("chunks"), f"{source}.chunks")
    chunks = [_mapping(value, f"{source}.chunks[]") for value in raw_chunks]
    issues: list[str] = []
    sequences = [_integer(chunk, "sequence") for chunk in chunks]
    expected_sequences = list(range(1, len(chunks) + 1))
    if sequences != expected_sequences:
        issues.append(f"{source}: chunk sequence is {sequences}, expected {expected_sequences}")

    total_frames = 0
    duration_seconds = 0.0
    first_start: int | None = None
    final_end: int | None = None
    previous_end: int | None = None
    maximum_gap_ns = 0
    maximum_overlap_ns = 0
    for chunk in chunks:
        filename = _string(chunk, "filename")
        if Path(filename).name != filename:
            raise CaptureAuditError(f"{source}: chunk filename must not contain a path")
        start_ns = _integer(chunk, "start_monotonic_ns")
        end_ns = _integer(chunk, "end_monotonic_ns")
        frame_count = _integer(chunk, "frame_count")
        byte_count = _integer(chunk, "byte_count")
        sample_rate = _integer(chunk, "sample_rate")
        channels = _integer(chunk, "channels")
        sample_width = _integer(chunk, "sample_width_bytes")
        if (
            start_ns < 0
            or end_ns < start_ns
            or frame_count < 1
            or sample_rate < 1
            or channels < 1
            or sample_width < 1
        ):
            raise CaptureAuditError(f"{source}: chunk timing and format values must be positive")
        expected_byte_count = frame_count * channels * sample_width
        if byte_count != expected_byte_count:
            issues.append(
                f"{source}: {filename} byte count {byte_count} does not match {expected_byte_count}"
            )
        expected_duration_ns = (frame_count * 1_000_000_000) // sample_rate
        if abs((end_ns - start_ns) - expected_duration_ns) > TIMELINE_TOLERANCE_NS:
            issues.append(f"{source}: {filename} timing does not match its frame count")
        if previous_end is not None:
            delta_ns = start_ns - previous_end
            maximum_gap_ns = max(maximum_gap_ns, delta_ns)
            maximum_overlap_ns = max(maximum_overlap_ns, -delta_ns)
        first_start = start_ns if first_start is None else min(first_start, start_ns)
        final_end = end_ns if final_end is None else max(final_end, end_ns)
        previous_end = end_ns
        total_frames += frame_count
        duration_seconds += frame_count / sample_rate
        if verify_audio:
            wav_issue = _verify_wav(
                audio_directory / filename,
                expected_frames=frame_count,
                expected_rate=sample_rate,
                expected_channels=channels,
                expected_sample_width=sample_width,
            )
            if wav_issue is not None:
                issues.append(wav_issue)

    max_gap_ms = maximum_gap_ns / 1_000_000
    max_overlap_ms = maximum_overlap_ns / 1_000_000
    if not chunks:
        issues.append(f"{source}: no finalized chunks")
    if duration_seconds < minimum_duration_minutes * 60:
        issues.append(
            f"{source}: duration {duration_seconds / 60:.2f} minutes is below the "
            f"required {minimum_duration_minutes:.2f} minutes"
        )
    if maximum_overlap_ns > TIMELINE_TOLERANCE_NS:
        issues.append(f"{source}: chunk timeline overlaps by up to {max_overlap_ms:.3f} ms")
    if maximum_gap_ms is not None and max_gap_ms > maximum_gap_ms:
        issues.append(
            f"{source}: chunk timeline gap {max_gap_ms:.3f} ms exceeds {maximum_gap_ms:.3f} ms"
        )
    return (
        SourceAudit(
            source=source,
            chunk_count=len(chunks),
            frame_count=total_frames,
            duration_seconds=duration_seconds,
            first_start_monotonic_ns=first_start,
            final_end_monotonic_ns=final_end,
            max_gap_ms=max_gap_ms,
            max_overlap_ms=max_overlap_ms,
        ),
        issues,
    )


def audit_capture_manifest(
    path: Path,
    *,
    maximum_drift_ms: float = DEFAULT_MAX_DRIFT_MS,
    minimum_duration_minutes: float = 0.0,
    maximum_gap_ms: float | None = None,
    verify_audio: bool = True,
) -> CaptureAuditReport:
    if maximum_drift_ms < 0 or minimum_duration_minutes < 0:
        raise ValueError("Audit drift and duration limits cannot be negative")
    if maximum_gap_ms is not None and maximum_gap_ms < 0:
        raise ValueError("Audit timeline gap limit cannot be negative")

    manifest_path = _manifest_path(path)
    try:
        raw_document: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CaptureAuditError(f"Could not read capture manifest: {manifest_path}") from error
    document = _mapping(raw_document, "capture manifest")
    session_id = _string(document, "session_id")
    raw_sources = _sequence(document.get("sources"), "sources")
    source_documents = [_mapping(value, "sources[]") for value in raw_sources]
    source_audits: list[SourceAudit] = []
    issues: list[str] = []
    for source_document in source_documents:
        source_audit, source_issues = _audit_source(
            source_document,
            manifest_path.parent / "audio",
            minimum_duration_minutes=minimum_duration_minutes,
            maximum_gap_ms=maximum_gap_ms,
            verify_audio=verify_audio,
        )
        source_audits.append(source_audit)
        issues.extend(source_issues)

    source_names = {source.source for source in source_audits}
    expected_sources = {"microphone", "system_loopback"}
    if source_names != expected_sources:
        issues.append(
            f"capture sources are {sorted(source_names)}, expected {sorted(expected_sources)}"
        )
    starts = [
        source.first_start_monotonic_ns
        for source in source_audits
        if source.first_start_monotonic_ns is not None
    ]
    ends = [
        source.final_end_monotonic_ns
        for source in source_audits
        if source.final_end_monotonic_ns is not None
    ]
    start_alignment_ms = (max(starts) - min(starts)) / 1_000_000 if len(starts) > 1 else 0.0
    end_alignment_ms = (max(ends) - min(ends)) / 1_000_000 if len(ends) > 1 else 0.0
    if end_alignment_ms > maximum_drift_ms:
        issues.append(
            f"source end alignment drift {end_alignment_ms:.3f} ms exceeds "
            f"{maximum_drift_ms:.3f} ms"
        )
    return CaptureAuditReport(
        session_id=session_id,
        sources=tuple(source_audits),
        start_alignment_ms=start_alignment_ms,
        end_alignment_drift_ms=end_alignment_ms,
        max_allowed_drift_ms=maximum_drift_ms,
        minimum_duration_minutes=minimum_duration_minutes,
        issues=tuple(issues),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit a capture journal for source alignment and chunk integrity."
    )
    parser.add_argument("path", type=Path, help="Session directory or capture.json path")
    parser.add_argument("--max-drift-ms", type=float, default=DEFAULT_MAX_DRIFT_MS)
    parser.add_argument("--min-duration-minutes", type=float, default=0.0)
    parser.add_argument(
        "--max-gap-ms",
        type=float,
        help="Fail if a source has a larger chunk timeline gap (pauses create gaps)",
    )
    parser.add_argument(
        "--skip-audio-files",
        action="store_true",
        help="Audit manifest metadata without checking WAV headers",
    )
    parser.add_argument("--json", action="store_true", help="Print the report as JSON")
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    try:
        report = audit_capture_manifest(
            options.path,
            maximum_drift_ms=options.max_drift_ms,
            minimum_duration_minutes=options.min_duration_minutes,
            maximum_gap_ms=options.max_gap_ms,
            verify_audio=not options.skip_audio_files,
        )
    except (CaptureAuditError, ValueError) as error:
        print(f"Capture audit could not run: {error}", file=sys.stderr)
        return 2
    if options.json:
        print(json.dumps({**asdict(report), "passed": report.passed}, indent=2))
    else:
        outcome = "PASS" if report.passed else "FAIL"
        print(f"Capture audit: {outcome}")
        print(f"Session: {report.session_id}")
        for source in report.sources:
            print(
                f"- {source.source}: {source.duration_seconds / 60:.2f} min, "
                f"{source.chunk_count} chunks, max gap {source.max_gap_ms:.3f} ms"
            )
        print(f"End alignment drift: {report.end_alignment_drift_ms:.3f} ms")
        for issue in report.issues:
            print(f"- ERROR: {issue}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import cast
from uuid import UUID

from meeting_transcriber.domain.transcript import TranscriptDocument, TranscriptSource
from meeting_transcriber.storage.transcript_store import TranscriptDataError, TranscriptStore


class AccuracyReferenceError(ValueError):
    """Raised when a human-reviewed accuracy reference is malformed."""


@dataclass(frozen=True, slots=True)
class AccuracyReferenceSegment:
    start_ms: int
    end_ms: int
    source: TranscriptSource
    text: str

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("Reference segment timestamps must have positive duration")
        if not self.text.strip():
            raise ValueError("Reference segment text cannot be blank")


@dataclass(frozen=True, slots=True)
class AccuracyReference:
    SCHEMA_VERSION = 1

    session_id: str
    language: str
    segments: tuple[AccuracyReferenceSegment, ...]
    key_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        UUID(self.session_id)
        if not self.language.strip():
            raise ValueError("Reference language cannot be blank")
        if any(not term.strip() for term in self.key_terms):
            raise ValueError("Reference key terms cannot be blank")

    @classmethod
    def load(cls, path: Path) -> AccuracyReference:
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise AccuracyReferenceError(f"Could not read accuracy reference: {path}") from error
        if not isinstance(raw, Mapping):
            raise AccuracyReferenceError("Accuracy reference must be a JSON object")
        document = cast(Mapping[str, object], raw)
        if document.get("schema_version") != cls.SCHEMA_VERSION:
            raise AccuracyReferenceError(
                f"Unsupported accuracy reference schema {document.get('schema_version')!r}"
            )
        try:
            session_id = _required_string(document, "session_id")
            language = _required_string(document, "language")
            raw_terms = _required_list(document, "key_terms")
            key_terms = tuple(_list_string(term, "key_terms[]") for term in raw_terms)
            segments = tuple(
                _reference_segment(item) for item in _required_list(document, "segments")
            )
            return cls(session_id, language, segments, key_terms)
        except (TypeError, ValueError) as error:
            if isinstance(error, AccuracyReferenceError):
                raise
            raise AccuracyReferenceError("Accuracy reference contains invalid values") from error


@dataclass(frozen=True, slots=True)
class AccuracyThresholds:
    max_word_error_rate: float | None = None
    min_key_term_recall: float | None = None
    min_source_accuracy: float | None = None
    max_mean_timing_error_ms: float | None = None
    max_hallucinated_tokens: int | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("max_word_error_rate", self.max_word_error_rate),
            ("min_key_term_recall", self.min_key_term_recall),
            ("min_source_accuracy", self.min_source_accuracy),
        ):
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between zero and one")
        if self.max_mean_timing_error_ms is not None and self.max_mean_timing_error_ms < 0:
            raise ValueError("max_mean_timing_error_ms cannot be negative")
        if self.max_hallucinated_tokens is not None and self.max_hallucinated_tokens < 0:
            raise ValueError("max_hallucinated_tokens cannot be negative")


@dataclass(frozen=True, slots=True)
class AccuracyReport:
    session_id: str
    run_id: str
    language: str
    model: str
    profile: str
    reference_tokens: int
    hypothesis_tokens: int
    substitutions: int
    deletions: int
    insertions: int
    word_error_rate: float | None
    hallucinated_tokens: int
    key_terms_found: int
    key_terms_total: int
    key_term_recall: float | None
    source_matches: int
    source_comparisons: int
    source_accuracy: float | None
    timing_comparisons: int
    mean_timing_error_ms: float | None

    def violations(self, thresholds: AccuracyThresholds) -> tuple[str, ...]:
        violations: list[str] = []
        if (
            thresholds.max_word_error_rate is not None
            and self.word_error_rate is not None
            and self.word_error_rate > thresholds.max_word_error_rate
        ):
            violations.append(
                f"WER {self.word_error_rate:.3f} exceeds {thresholds.max_word_error_rate:.3f}"
            )
        if (
            thresholds.min_key_term_recall is not None
            and self.key_term_recall is not None
            and self.key_term_recall < thresholds.min_key_term_recall
        ):
            violations.append(
                "key-term recall "
                f"{self.key_term_recall:.3f} is below {thresholds.min_key_term_recall:.3f}"
            )
        if (
            thresholds.min_source_accuracy is not None
            and self.source_accuracy is not None
            and self.source_accuracy < thresholds.min_source_accuracy
        ):
            violations.append(
                "source accuracy "
                f"{self.source_accuracy:.3f} is below {thresholds.min_source_accuracy:.3f}"
            )
        if (
            thresholds.max_mean_timing_error_ms is not None
            and self.mean_timing_error_ms is not None
            and self.mean_timing_error_ms > thresholds.max_mean_timing_error_ms
        ):
            violations.append(
                "mean timing error "
                f"{self.mean_timing_error_ms:.1f} ms exceeds "
                f"{thresholds.max_mean_timing_error_ms:.1f} ms"
            )
        if (
            thresholds.max_hallucinated_tokens is not None
            and self.hallucinated_tokens > thresholds.max_hallucinated_tokens
        ):
            violations.append(
                f"hallucinated tokens {self.hallucinated_tokens} exceeds "
                f"{thresholds.max_hallucinated_tokens}"
            )
        return tuple(violations)

    def to_document(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "language": self.language,
            "model": self.model,
            "profile": self.profile,
            "reference_tokens": self.reference_tokens,
            "hypothesis_tokens": self.hypothesis_tokens,
            "substitutions": self.substitutions,
            "deletions": self.deletions,
            "insertions": self.insertions,
            "word_error_rate": self.word_error_rate,
            "hallucinated_tokens": self.hallucinated_tokens,
            "key_terms_found": self.key_terms_found,
            "key_terms_total": self.key_terms_total,
            "key_term_recall": self.key_term_recall,
            "source_matches": self.source_matches,
            "source_comparisons": self.source_comparisons,
            "source_accuracy": self.source_accuracy,
            "timing_comparisons": self.timing_comparisons,
            "mean_timing_error_ms": self.mean_timing_error_ms,
        }


@dataclass(frozen=True, slots=True)
class _TokenEvidence:
    text: str
    source: TranscriptSource
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class _EditCounts:
    substitutions: int = 0
    deletions: int = 0
    insertions: int = 0

    @property
    def total(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def rank(self) -> tuple[int, int, int, int]:
        return (self.total, self.substitutions, self.deletions, self.insertions)

    def substitution(self) -> _EditCounts:
        return _EditCounts(self.substitutions + 1, self.deletions, self.insertions)

    def deletion(self) -> _EditCounts:
        return _EditCounts(self.substitutions, self.deletions + 1, self.insertions)

    def insertion(self) -> _EditCounts:
        return _EditCounts(self.substitutions, self.deletions, self.insertions + 1)


def normalize_tokens(text: str) -> tuple[str, ...]:
    """Normalize meeting text into Unicode-aware WER tokens.

    CJK ideographs are individual tokens so languages without whitespace are not
    treated as a single meeting-long word.
    """

    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            tokens.append("".join(current))
            current.clear()

    for character in normalized:
        category = unicodedata.category(character)
        if _is_cjk(character):
            flush()
            tokens.append(character)
        elif category[0] in {"L", "N"} or category in {"Mn", "Mc"}:
            current.append(character)
        elif character in {"'", "\u2019"} and current:
            current.append("'")
        else:
            flush()
    flush()
    return tuple(tokens)


def evaluate_accuracy(
    transcript: TranscriptDocument,
    reference: AccuracyReference,
) -> AccuracyReport:
    if transcript.session_id != reference.session_id:
        raise ValueError("Transcript and reference session IDs do not match")
    reference_evidence = _reference_evidence(reference)
    hypothesis_evidence = _transcript_evidence(transcript)
    reference_tokens = tuple(item.text for item in reference_evidence)
    hypothesis_tokens = tuple(item.text for item in hypothesis_evidence)
    edits = _edit_counts(reference_tokens, hypothesis_tokens)
    word_error_rate = edits.total / len(reference_tokens) if reference_tokens else None
    hallucinated_tokens = edits.insertions if not reference_tokens else 0

    key_terms_found = sum(
        _contains_sequence(hypothesis_tokens, normalize_tokens(term))
        for term in reference.key_terms
    )
    key_term_recall = key_terms_found / len(reference.key_terms) if reference.key_terms else None

    source_matches = 0
    source_comparisons = 0
    timing_errors: list[float] = []
    matcher = SequenceMatcher(None, reference_tokens, hypothesis_tokens, autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            expected = reference_evidence[block.a + offset]
            actual = hypothesis_evidence[block.b + offset]
            source_comparisons += 1
            source_matches += expected.source is actual.source
            timing_errors.append(
                (abs(expected.start_ms - actual.start_ms) + abs(expected.end_ms - actual.end_ms))
                / 2
            )

    return AccuracyReport(
        session_id=transcript.session_id,
        run_id=transcript.run_id,
        language=reference.language,
        model=transcript.model,
        profile=transcript.profile.value,
        reference_tokens=len(reference_tokens),
        hypothesis_tokens=len(hypothesis_tokens),
        substitutions=edits.substitutions,
        deletions=edits.deletions,
        insertions=edits.insertions,
        word_error_rate=word_error_rate,
        hallucinated_tokens=hallucinated_tokens,
        key_terms_found=key_terms_found,
        key_terms_total=len(reference.key_terms),
        key_term_recall=key_term_recall,
        source_matches=source_matches,
        source_comparisons=source_comparisons,
        source_accuracy=(source_matches / source_comparisons if source_comparisons else None),
        timing_comparisons=len(timing_errors),
        mean_timing_error_ms=(sum(timing_errors) / len(timing_errors) if timing_errors else None),
    )


def _reference_evidence(reference: AccuracyReference) -> tuple[_TokenEvidence, ...]:
    return tuple(
        _TokenEvidence(token, segment.source, segment.start_ms, segment.end_ms)
        for segment in reference.segments
        for token in normalize_tokens(segment.text)
    )


def _transcript_evidence(transcript: TranscriptDocument) -> tuple[_TokenEvidence, ...]:
    evidence: list[_TokenEvidence] = []
    for segment in transcript.segments:
        if segment.words:
            evidence.extend(
                _TokenEvidence(token, segment.source, segment.start_ms, segment.end_ms)
                for word in segment.words
                for token in normalize_tokens(word.text)
            )
        else:
            evidence.extend(
                _TokenEvidence(token, segment.source, segment.start_ms, segment.end_ms)
                for token in normalize_tokens(segment.text)
            )
    return tuple(evidence)


def _edit_counts(reference: Sequence[str], hypothesis: Sequence[str]) -> _EditCounts:
    previous = [_EditCounts(insertions=index) for index in range(len(hypothesis) + 1)]
    for reference_index, reference_token in enumerate(reference, start=1):
        current = [_EditCounts(deletions=reference_index)]
        for hypothesis_index, hypothesis_token in enumerate(hypothesis, start=1):
            if reference_token == hypothesis_token:
                current.append(previous[hypothesis_index - 1])
                continue
            candidates = (
                previous[hypothesis_index - 1].substitution(),
                previous[hypothesis_index].deletion(),
                current[hypothesis_index - 1].insertion(),
            )
            current.append(min(candidates, key=lambda candidate: candidate.rank))
        previous = current
    return previous[-1]


def _contains_sequence(tokens: Sequence[str], phrase: Sequence[str]) -> int:
    if not phrase or len(phrase) > len(tokens):
        return 0
    return int(
        any(
            tuple(tokens[index : index + len(phrase)]) == tuple(phrase)
            for index in range(len(tokens))
        )
    )


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def _required_string(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AccuracyReferenceError(f"{field} must be a non-empty string")
    return value


def _required_list(document: Mapping[str, object], field: str) -> list[object]:
    value = document.get(field)
    if not isinstance(value, list):
        raise AccuracyReferenceError(f"{field} must be a JSON array")
    return cast(list[object], value)


def _list_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AccuracyReferenceError(f"{field} must be a non-empty string")
    return value


def _reference_segment(value: object) -> AccuracyReferenceSegment:
    if not isinstance(value, Mapping):
        raise AccuracyReferenceError("segments[] must be a JSON object")
    segment = cast(Mapping[str, object], value)
    start_ms = segment.get("start_ms")
    end_ms = segment.get("end_ms")
    if isinstance(start_ms, bool) or not isinstance(start_ms, int):
        raise AccuracyReferenceError("segments[].start_ms must be an integer")
    if isinstance(end_ms, bool) or not isinstance(end_ms, int):
        raise AccuracyReferenceError("segments[].end_ms must be an integer")
    return AccuracyReferenceSegment(
        start_ms,
        end_ms,
        TranscriptSource(_required_string(segment, "source")),
        _required_string(segment, "text"),
    )


def _metric(value: float | None, *, percent: bool = False, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if percent:
        return f"{value * 100:.1f}%"
    return f"{value:.1f}{suffix}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare a local transcript with a human-reviewed JSON reference."
    )
    parser.add_argument("transcript", type=Path, help="Path to transcript.json")
    parser.add_argument("reference", type=Path, help="Path to an accuracy reference JSON file")
    parser.add_argument("--max-wer", type=float)
    parser.add_argument("--min-key-term-recall", type=float)
    parser.add_argument("--min-source-accuracy", type=float)
    parser.add_argument("--max-mean-timing-error-ms", type=float)
    parser.add_argument("--max-hallucinated-tokens", type=int)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        transcript = TranscriptStore.load_transcript_path(arguments.transcript)
        reference = AccuracyReference.load(arguments.reference)
        thresholds = AccuracyThresholds(
            max_word_error_rate=arguments.max_wer,
            min_key_term_recall=arguments.min_key_term_recall,
            min_source_accuracy=arguments.min_source_accuracy,
            max_mean_timing_error_ms=arguments.max_mean_timing_error_ms,
            max_hallucinated_tokens=arguments.max_hallucinated_tokens,
        )
        report = evaluate_accuracy(transcript, reference)
    except (AccuracyReferenceError, TranscriptDataError, OSError, ValueError) as error:
        print(f"Accuracy evaluation failed: {error}", file=sys.stderr)
        return 2

    violations = report.violations(thresholds)
    if arguments.json:
        document = report.to_document()
        document["threshold_violations"] = list(violations)
        print(json.dumps(document, indent=2, ensure_ascii=False))
    else:
        print(f"Model/profile: {report.model} / {report.profile}")
        print(
            f"WER: {_metric(report.word_error_rate, percent=True)} "
            f"(S={report.substitutions}, D={report.deletions}, I={report.insertions})"
        )
        print(
            "Key-term recall: "
            f"{_metric(report.key_term_recall, percent=True)} "
            f"({report.key_terms_found}/{report.key_terms_total})"
        )
        print(
            "Source accuracy: "
            f"{_metric(report.source_accuracy, percent=True)} "
            f"({report.source_matches}/{report.source_comparisons})"
        )
        print(
            "Mean timing error: "
            f"{_metric(report.mean_timing_error_ms, suffix=' ms')} "
            f"({report.timing_comparisons} matched tokens)"
        )
        print(f"Hallucinated tokens in silence: {report.hallucinated_tokens}")
        for violation in violations:
            print(f"FAIL: {violation}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())

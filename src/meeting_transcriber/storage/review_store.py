from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from meeting_transcriber.domain.review import (
    SegmentSpeakerCorrection,
    SegmentTextCorrection,
    SpeakerNameCorrection,
    StructuredNotesCorrection,
    TranscriptReview,
)
from meeting_transcriber.domain.transcript import TranscriptDocument
from meeting_transcriber.storage.session_paths import SessionPathError, resolve_session_directory


class ReviewDataError(ValueError):
    """Raised when a persisted transcript review is malformed."""


class ReviewNotFoundError(FileNotFoundError):
    """Raised when a meeting has no persisted transcript review."""


class ReviewStore:
    """Persist the current sparse review plus immutable revision snapshots."""

    def __init__(self, meeting_root: Path):
        self.meeting_root = meeting_root

    def review_file(self, session_id: str) -> Path:
        return self._session_directory(session_id) / "transcript-review.json"

    def revision_file(self, review: TranscriptReview) -> Path:
        return (
            self._session_directory(review.session_id)
            / "derived"
            / "reviews"
            / review.run_id
            / f"revision-{review.revision:06d}.json"
        )

    def save(self, review: TranscriptReview) -> Path:
        if review.revision < 1:
            raise ReviewDataError("Only a user or migrated review revision can be saved")
        document = _review_document(review)
        self._save_document(self.revision_file(review), document)
        canonical = self.review_file(review.session_id)
        self._save_document(canonical, document)
        return canonical

    def load(self, session_id: str) -> TranscriptReview:
        path = self.review_file(session_id)
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ReviewNotFoundError(f"Transcript review not found: {path}") from error
        except (OSError, json.JSONDecodeError) as error:
            raise ReviewDataError(f"Could not read transcript review: {path}") from error
        review = _parse_review(_mapping(raw, "Transcript review"))
        if review.session_id != str(UUID(session_id)):
            raise ReviewDataError("Transcript review session ID does not match its directory")
        return review

    def load_for_transcript(self, transcript: TranscriptDocument) -> TranscriptReview:
        try:
            review = self.load(transcript.session_id)
        except ReviewNotFoundError:
            return TranscriptReview.new(transcript)
        if review.run_id == transcript.run_id:
            review.apply(transcript)
            return review
        return review.migrate_speaker_names(transcript)

    def _session_directory(self, session_id: str) -> Path:
        try:
            return resolve_session_directory(self.meeting_root, session_id)
        except SessionPathError as error:
            raise ReviewDataError(str(error)) from error

    @staticmethod
    def _save_document(path: Path, document: Mapping[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f"{path.stem}-",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)


def _review_document(review: TranscriptReview) -> dict[str, object]:
    return {
        "schema_version": TranscriptReview.SCHEMA_VERSION,
        "session_id": review.session_id,
        "run_id": review.run_id,
        "revision": review.revision,
        "updated_at": review.updated_at.isoformat().replace("+00:00", "Z"),
        "speaker_names": [
            {
                "speaker_id": correction.speaker_id,
                "display_name": correction.display_name,
            }
            for correction in review.speaker_names
        ],
        "segment_texts": [
            {
                "segment_id": correction.segment_id,
                "text": correction.text,
            }
            for correction in review.segment_texts
        ],
        "segment_speakers": [
            {
                "segment_id": correction.segment_id,
                "speaker_id": correction.speaker_id,
            }
            for correction in review.segment_speakers
        ],
        "structured_notes": (
            {
                "summary": review.structured_notes.summary,
                "decisions": list(review.structured_notes.decisions),
                "action_items": list(review.structured_notes.action_items),
            }
            if review.structured_notes is not None
            else None
        ),
    }


def _parse_review(document: Mapping[str, object]) -> TranscriptReview:
    schema_version = document.get("schema_version")
    if schema_version not in {1, 2, TranscriptReview.SCHEMA_VERSION}:
        raise ReviewDataError(
            f"Unsupported transcript review schema {document.get('schema_version')!r}"
        )
    try:
        updated_at = datetime.fromisoformat(_string(document, "updated_at"))
        if updated_at.tzinfo is None or updated_at.utcoffset() is None:
            raise ReviewDataError("updated_at must include a timezone")
        speaker_names = tuple(
            SpeakerNameCorrection(
                _string(correction, "speaker_id"),
                _string(correction, "display_name"),
            )
            for correction in (
                _mapping(item, "speaker_names[]") for item in _list(document, "speaker_names")
            )
        )
        segment_texts = tuple(
            SegmentTextCorrection(
                _string(correction, "segment_id"),
                _string(correction, "text"),
            )
            for correction in (
                _mapping(item, "segment_texts[]") for item in _list(document, "segment_texts")
            )
        )
        segment_speakers = (
            tuple(
                SegmentSpeakerCorrection(
                    _string(correction, "segment_id"),
                    _string(correction, "speaker_id"),
                )
                for correction in (
                    _mapping(item, "segment_speakers[]")
                    for item in _list(document, "segment_speakers")
                )
            )
            if schema_version in {2, TranscriptReview.SCHEMA_VERSION}
            else ()
        )
        structured_notes = (
            _parse_structured_notes(document.get("structured_notes"))
            if schema_version == TranscriptReview.SCHEMA_VERSION
            else None
        )
        revision = document.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise ReviewDataError("revision must be an integer")
        return TranscriptReview(
            _string(document, "session_id"),
            _string(document, "run_id"),
            revision,
            updated_at,
            speaker_names,
            segment_texts,
            segment_speakers,
            structured_notes,
        )
    except (TypeError, ValueError) as error:
        if isinstance(error, ReviewDataError):
            raise
        raise ReviewDataError("Transcript review contains invalid values") from error


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReviewDataError(f"{field} must be a JSON object")
    return cast(Mapping[str, object], value)


def _list(document: Mapping[str, object], field: str) -> list[object]:
    value = document.get(field)
    if not isinstance(value, list):
        raise ReviewDataError(f"{field} must be a JSON array")
    return cast(list[object], value)


def _string(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise ReviewDataError(f"{field} must be a non-empty string")
    return value


def _parse_structured_notes(value: object) -> StructuredNotesCorrection | None:
    if value is None:
        return None
    document = _mapping(value, "structured_notes")
    summary = document.get("summary")
    if not isinstance(summary, str):
        raise ReviewDataError("structured_notes.summary must be a string")
    return StructuredNotesCorrection(
        summary,
        tuple(
            _string_value(item, "structured_notes.decisions[]")
            for item in _list(document, "decisions")
        ),
        tuple(
            _string_value(item, "structured_notes.action_items[]")
            for item in _list(document, "action_items")
        ),
    )


def _string_value(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewDataError(f"{field} must be a non-empty string")
    return value

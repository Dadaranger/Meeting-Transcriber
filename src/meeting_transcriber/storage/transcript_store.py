from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from meeting_transcriber.domain.transcript import (
    TranscriptDocument,
    TranscriptionJob,
    TranscriptionJobState,
    TranscriptionProfile,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
    TranscriptWord,
)


class TranscriptDataError(ValueError):
    """Raised when a persisted transcript or transcription job is malformed."""


class UnsupportedTranscriptSchema(TranscriptDataError):
    """Raised when a transcript artifact uses an unknown schema version."""


class TranscriptNotFoundError(FileNotFoundError):
    """Raised when a requested transcript artifact does not exist."""


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise TranscriptDataError(f"{field} must be an ISO-8601 timestamp")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise TranscriptDataError(f"{field} must be an ISO-8601 timestamp") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise TranscriptDataError(f"{field} must include a timezone")
    return timestamp


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TranscriptDataError(f"{field} must be a JSON object")
    return cast(Mapping[str, object], value)


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise TranscriptDataError(f"{field} must be a JSON array")
    return cast(list[object], value)


def _string(document: Mapping[str, object], field: str, *, optional: bool = False) -> str | None:
    value = document.get(field)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise TranscriptDataError(f"{field} must be a non-empty string")
    return value


def _integer(document: Mapping[str, object], field: str) -> int:
    value = document.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TranscriptDataError(f"{field} must be an integer")
    return value


def _optional_integer(document: Mapping[str, object], field: str) -> int | None:
    if document.get(field) is None:
        return None
    return _integer(document, field)


def _boolean(document: Mapping[str, object], field: str) -> bool:
    value = document.get(field)
    if not isinstance(value, bool):
        raise TranscriptDataError(f"{field} must be a boolean")
    return value


def _probability(document: Mapping[str, object], field: str) -> float | None:
    value = document.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TranscriptDataError(f"{field} must be a number or null")
    return float(value)


def _transcript_document(transcript: TranscriptDocument) -> dict[str, object]:
    return {
        "schema_version": TranscriptDocument.SCHEMA_VERSION,
        "session_id": transcript.session_id,
        "run_id": transcript.run_id,
        "language": transcript.language,
        "engine": transcript.engine,
        "model": transcript.model,
        "profile": transcript.profile.value,
        "created_at": _timestamp(transcript.created_at),
        "speakers": [
            {
                "speaker_id": speaker.speaker_id,
                "display_name": speaker.display_name,
                "source": speaker.source.value,
            }
            for speaker in transcript.speakers
        ],
        "segments": [
            {
                "segment_id": segment.segment_id,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "speaker_id": segment.speaker_id,
                "text": segment.text,
                "source": segment.source.value,
                "confidence": segment.confidence,
                "words": [
                    {
                        "text": word.text,
                        "start_ms": word.start_ms,
                        "end_ms": word.end_ms,
                        "probability": word.probability,
                    }
                    for word in segment.words
                ],
            }
            for segment in transcript.segments
        ],
    }


def _parse_transcript(document: Mapping[str, object]) -> TranscriptDocument:
    if document.get("schema_version") != TranscriptDocument.SCHEMA_VERSION:
        raise UnsupportedTranscriptSchema(
            f"Unsupported transcript schema {document.get('schema_version')!r}"
        )
    try:
        speakers = tuple(
            TranscriptSpeaker(
                speaker_id=cast(str, _string(speaker, "speaker_id")),
                display_name=cast(str, _string(speaker, "display_name")),
                source=TranscriptSource(cast(str, _string(speaker, "source"))),
            )
            for speaker in (
                _mapping(value, "speakers[]")
                for value in _list(document.get("speakers"), "speakers")
            )
        )
        segments: list[TranscriptSegment] = []
        for raw_segment in _list(document.get("segments"), "segments"):
            segment = _mapping(raw_segment, "segments[]")
            words = tuple(
                TranscriptWord(
                    text=cast(str, _string(word, "text")),
                    start_ms=_integer(word, "start_ms"),
                    end_ms=_integer(word, "end_ms"),
                    probability=_probability(word, "probability"),
                )
                for word in (
                    _mapping(value, "segments[].words[]")
                    for value in _list(segment.get("words"), "segments[].words")
                )
            )
            segments.append(
                TranscriptSegment(
                    segment_id=cast(str, _string(segment, "segment_id")),
                    start_ms=_integer(segment, "start_ms"),
                    end_ms=_integer(segment, "end_ms"),
                    speaker_id=cast(str, _string(segment, "speaker_id")),
                    text=cast(str, _string(segment, "text")),
                    source=TranscriptSource(cast(str, _string(segment, "source"))),
                    confidence=_probability(segment, "confidence"),
                    words=words,
                )
            )
        return TranscriptDocument(
            session_id=cast(str, _string(document, "session_id")),
            run_id=cast(str, _string(document, "run_id")),
            language=cast(str, _string(document, "language")),
            engine=cast(str, _string(document, "engine")),
            model=cast(str, _string(document, "model")),
            profile=TranscriptionProfile(cast(str, _string(document, "profile"))),
            created_at=_parse_timestamp(document.get("created_at"), "created_at"),
            speakers=speakers,
            segments=tuple(segments),
        )
    except (TypeError, ValueError) as error:
        if isinstance(error, TranscriptDataError):
            raise
        raise TranscriptDataError("Transcript document contains invalid values") from error


def _job_document(job: TranscriptionJob) -> dict[str, object]:
    return {
        "schema_version": TranscriptionJob.SCHEMA_VERSION,
        "job_id": job.job_id,
        "session_id": job.session_id,
        "state": job.state.value,
        "profile": job.profile.value,
        "language": job.language,
        "created_at": _timestamp(job.created_at),
        "updated_at": _timestamp(job.updated_at),
        "attempt": job.attempt,
        "processed_audio_ms": job.processed_audio_ms,
        "total_audio_ms": job.total_audio_ms,
        "model_downloaded_bytes": job.model_downloaded_bytes,
        "model_total_bytes": job.model_total_bytes,
        "error": job.error,
        "separate_remote_speakers": job.separate_remote_speakers,
        "min_remote_speakers": job.min_remote_speakers,
        "max_remote_speakers": job.max_remote_speakers,
        "warning": job.warning,
    }


def _parse_job(document: Mapping[str, object]) -> TranscriptionJob:
    schema_version = document.get("schema_version")
    if schema_version not in {1, 2, TranscriptionJob.SCHEMA_VERSION}:
        raise UnsupportedTranscriptSchema(
            f"Unsupported transcription job schema {document.get('schema_version')!r}"
        )
    try:
        return TranscriptionJob(
            job_id=cast(str, _string(document, "job_id")),
            session_id=cast(str, _string(document, "session_id")),
            state=TranscriptionJobState(cast(str, _string(document, "state"))),
            profile=TranscriptionProfile(cast(str, _string(document, "profile"))),
            language=_string(document, "language", optional=True),
            created_at=_parse_timestamp(document.get("created_at"), "created_at"),
            updated_at=_parse_timestamp(document.get("updated_at"), "updated_at"),
            attempt=_integer(document, "attempt"),
            processed_audio_ms=_integer(document, "processed_audio_ms"),
            total_audio_ms=_integer(document, "total_audio_ms"),
            model_downloaded_bytes=(
                _integer(document, "model_downloaded_bytes") if schema_version == 3 else 0
            ),
            model_total_bytes=(
                _integer(document, "model_total_bytes") if schema_version == 3 else 0
            ),
            error=_string(document, "error", optional=True),
            separate_remote_speakers=(
                _boolean(document, "separate_remote_speakers")
                if schema_version in {2, 3}
                else False
            ),
            min_remote_speakers=(
                _optional_integer(document, "min_remote_speakers")
                if schema_version in {2, 3}
                else None
            ),
            max_remote_speakers=(
                _optional_integer(document, "max_remote_speakers")
                if schema_version in {2, 3}
                else None
            ),
            warning=(
                _string(document, "warning", optional=True) if schema_version in {2, 3} else None
            ),
        )
    except (TypeError, ValueError) as error:
        if isinstance(error, TranscriptDataError):
            raise
        raise TranscriptDataError("Transcription job document contains invalid values") from error


class TranscriptStore:
    """Atomically persist canonical transcripts, retained runs, and active job state."""

    def __init__(self, meeting_root: Path):
        self.meeting_root = meeting_root

    def transcript_file(self, session_id: str, run_id: str | None = None) -> Path:
        directory = self._session_directory(session_id)
        if run_id is None:
            return directory / "transcript.json"
        UUID(run_id)
        return directory / "derived" / "transcripts" / f"{run_id}.json"

    def job_file(self, session_id: str) -> Path:
        return self._session_directory(session_id) / "processing" / "transcription-job.json"

    def save_transcript(self, transcript: TranscriptDocument) -> Path:
        document = _transcript_document(transcript)
        retained_path = self.transcript_file(transcript.session_id, transcript.run_id)
        self._save_document(retained_path, document)
        canonical_path = self.transcript_file(transcript.session_id)
        self._save_document(canonical_path, document)
        return canonical_path

    def load_transcript(self, session_id: str, run_id: str | None = None) -> TranscriptDocument:
        path = self.transcript_file(session_id, run_id)
        transcript = self.load_transcript_path(path)
        if transcript.session_id != str(UUID(session_id)):
            raise TranscriptDataError("Transcript session ID does not match its directory")
        if run_id is not None and transcript.run_id != str(UUID(run_id)):
            raise TranscriptDataError("Transcript run ID does not match its filename")
        return transcript

    @staticmethod
    def load_transcript_path(path: Path) -> TranscriptDocument:
        """Load a transcript artifact without assuming its meeting-root location."""

        document = TranscriptStore._load_document(path, "Transcript")
        return _parse_transcript(document)

    def save_job(self, job: TranscriptionJob) -> Path:
        path = self.job_file(job.session_id)
        self._save_document(path, _job_document(job))
        return path

    def load_job(self, session_id: str) -> TranscriptionJob:
        path = self.job_file(session_id)
        document = self._load_document(path, "Transcription job")
        job = _parse_job(document)
        if job.session_id != str(UUID(session_id)):
            raise TranscriptDataError("Transcription job session ID does not match its directory")
        return job

    def _session_directory(self, session_id: str) -> Path:
        try:
            normalized = str(UUID(session_id))
        except ValueError as error:
            raise TranscriptDataError("session_id must be a UUID") from error
        return self.meeting_root / normalized

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

    @staticmethod
    def _load_document(path: Path, artifact_name: str) -> Mapping[str, object]:
        try:
            raw_document: object = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise TranscriptNotFoundError(f"{artifact_name} not found: {path}") from error
        except (OSError, json.JSONDecodeError) as error:
            raise TranscriptDataError(f"Could not read {artifact_name.lower()}: {path}") from error
        return _mapping(raw_document, artifact_name)

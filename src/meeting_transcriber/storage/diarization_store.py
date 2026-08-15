from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from meeting_transcriber.domain.diarization import DiarizationDocument, DiarizationTurn
from meeting_transcriber.storage.session_paths import SessionPathError, resolve_session_directory


class DiarizationDataError(ValueError):
    """Raised when a persisted diarization artifact is malformed."""


class DiarizationNotFoundError(FileNotFoundError):
    """Raised when a diarization artifact does not exist."""


class DiarizationStore:
    """Atomically persist canonical diarization and retained per-run artifacts."""

    def __init__(self, meeting_root: Path):
        self.meeting_root = meeting_root

    def diarization_file(self, session_id: str, run_id: str | None = None) -> Path:
        directory = self._session_directory(session_id)
        if run_id is None:
            return directory / "diarization.json"
        return directory / "derived" / "diarization" / f"{self._uuid(run_id, 'run_id')}.json"

    def save(self, document: DiarizationDocument) -> Path:
        serialized = _serialize(document)
        self._save_document(
            self.diarization_file(document.session_id, document.run_id),
            serialized,
        )
        canonical = self.diarization_file(document.session_id)
        self._save_document(canonical, serialized)
        return canonical

    def load(self, session_id: str, run_id: str | None = None) -> DiarizationDocument:
        path = self.diarization_file(session_id, run_id)
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise DiarizationNotFoundError(f"Diarization not found: {path}") from error
        except (OSError, json.JSONDecodeError) as error:
            raise DiarizationDataError(f"Could not read diarization: {path}") from error
        document = _parse(_mapping(raw, "Diarization"))
        if document.session_id != str(UUID(session_id)):
            raise DiarizationDataError("Diarization session ID does not match its directory")
        if run_id is not None and document.run_id != str(UUID(run_id)):
            raise DiarizationDataError("Diarization run ID does not match its filename")
        return document

    def _session_directory(self, session_id: str) -> Path:
        try:
            return resolve_session_directory(self.meeting_root, session_id)
        except SessionPathError as error:
            raise DiarizationDataError(str(error)) from error

    @staticmethod
    def _uuid(value: str, field: str) -> str:
        try:
            return str(UUID(value))
        except ValueError as error:
            raise DiarizationDataError(f"{field} must be a UUID") from error

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


def _serialize(document: DiarizationDocument) -> dict[str, object]:
    return {
        "schema_version": DiarizationDocument.SCHEMA_VERSION,
        "session_id": document.session_id,
        "run_id": document.run_id,
        "engine": document.engine,
        "model": document.model,
        "created_at": document.created_at.isoformat().replace("+00:00", "Z"),
        "turns": [
            {
                "start_ms": turn.start_ms,
                "end_ms": turn.end_ms,
                "speaker_id": turn.speaker_id,
            }
            for turn in document.turns
        ],
    }


def _parse(document: Mapping[str, object]) -> DiarizationDocument:
    if document.get("schema_version") != DiarizationDocument.SCHEMA_VERSION:
        raise DiarizationDataError(
            f"Unsupported diarization schema {document.get('schema_version')!r}"
        )
    try:
        created_at = datetime.fromisoformat(_string(document, "created_at"))
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise DiarizationDataError("created_at must include a timezone")
        turns = tuple(
            DiarizationTurn(
                _integer(turn, "start_ms"),
                _integer(turn, "end_ms"),
                _string(turn, "speaker_id"),
            )
            for turn in (_mapping(item, "turns[]") for item in _list(document, "turns"))
        )
        return DiarizationDocument(
            _string(document, "session_id"),
            _string(document, "run_id"),
            _string(document, "engine"),
            _string(document, "model"),
            created_at,
            turns,
        )
    except (TypeError, ValueError) as error:
        if isinstance(error, DiarizationDataError):
            raise
        raise DiarizationDataError("Diarization contains invalid values") from error


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DiarizationDataError(f"{field} must be a JSON object")
    return cast(Mapping[str, object], value)


def _list(document: Mapping[str, object], field: str) -> list[object]:
    value = document.get(field)
    if not isinstance(value, list):
        raise DiarizationDataError(f"{field} must be a JSON array")
    return cast(list[object], value)


def _string(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise DiarizationDataError(f"{field} must be a non-empty string")
    return value


def _integer(document: Mapping[str, object], field: str) -> int:
    value = document.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DiarizationDataError(f"{field} must be an integer")
    return value

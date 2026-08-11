from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from meeting_transcriber.domain.session import (
    ConsentCaptureSource,
    MeetingSession,
    SessionState,
)


class SessionDataError(ValueError):
    """Raised when a persisted session document is malformed."""


class UnsupportedSessionSchema(SessionDataError):
    """Raised when a persisted session uses an unknown schema version."""


class SessionNotFoundError(FileNotFoundError):
    """Raised when a requested meeting session does not exist."""


def _format_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, field: str, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise SessionDataError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise SessionDataError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SessionDataError(f"{field} must include a timezone")
    return parsed


def _required_string(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise SessionDataError(f"{field} must be a non-empty string")
    return value


def _to_document(session: MeetingSession) -> dict[str, object]:
    consent: dict[str, object] | None = None
    if session.consent_confirmed_at is not None:
        consent = {
            "confirmed_at": _format_timestamp(session.consent_confirmed_at),
            "text_version": session.consent_text_version,
            "capture_sources": [source.value for source in session.consent_capture_sources],
        }
    return {
        "schema_version": MeetingSession.SCHEMA_VERSION,
        "session_id": session.session_id,
        "title": session.title,
        "state": session.state.value,
        "created_at": _format_timestamp(session.created_at),
        "updated_at": _format_timestamp(session.updated_at),
        "revision": session.revision,
        "consent": consent,
        "started_at": _format_timestamp(session.started_at),
        "stopped_at": _format_timestamp(session.stopped_at),
    }


def _parse_consent(
    document: Mapping[str, object],
    schema_version: int,
) -> tuple[datetime | None, int | None, tuple[ConsentCaptureSource, ...]]:
    if schema_version == 1:
        confirmed_at = _parse_timestamp(
            document.get("consent_confirmed_at"),
            "consent_confirmed_at",
            optional=True,
        )
        return confirmed_at, 0 if confirmed_at is not None else None, ()

    raw_consent = document.get("consent")
    if raw_consent is None:
        return None, None, ()
    if not isinstance(raw_consent, Mapping):
        raise SessionDataError("consent must be a JSON object or null")

    confirmed_at = _parse_timestamp(raw_consent.get("confirmed_at"), "consent.confirmed_at")
    text_version = raw_consent.get("text_version")
    if isinstance(text_version, bool) or not isinstance(text_version, int):
        raise SessionDataError("consent.text_version must be an integer")

    raw_sources = raw_consent.get("capture_sources")
    if not isinstance(raw_sources, list) or not all(
        isinstance(source, str) for source in raw_sources
    ):
        raise SessionDataError("consent.capture_sources must be a list of strings")
    try:
        capture_sources = tuple(ConsentCaptureSource(source) for source in raw_sources)
    except ValueError as error:
        raise SessionDataError("consent.capture_sources contains an unknown source") from error
    return confirmed_at, text_version, capture_sources


def _from_document(document: Mapping[str, object]) -> MeetingSession:
    schema_version = document.get("schema_version")
    if schema_version not in {1, MeetingSession.SCHEMA_VERSION}:
        raise UnsupportedSessionSchema(
            f"Unsupported session schema {schema_version!r}; expected 1 or "
            f"{MeetingSession.SCHEMA_VERSION}"
        )
    if not isinstance(schema_version, int):
        raise UnsupportedSessionSchema(f"Unsupported session schema {schema_version!r}")

    revision = document.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise SessionDataError("revision must be an integer")

    state_value = _required_string(document, "state")
    try:
        state = SessionState(state_value)
    except ValueError as error:
        raise SessionDataError(f"Unknown meeting session state: {state_value}") from error

    created_at = _parse_timestamp(document.get("created_at"), "created_at")
    updated_at = _parse_timestamp(document.get("updated_at"), "updated_at")
    if created_at is None or updated_at is None:
        raise SessionDataError("created_at and updated_at are required")
    consent_confirmed_at, consent_text_version, consent_capture_sources = _parse_consent(
        document, schema_version
    )

    try:
        return MeetingSession(
            session_id=_required_string(document, "session_id"),
            title=_required_string(document, "title"),
            state=state,
            created_at=created_at,
            updated_at=updated_at,
            revision=revision,
            consent_confirmed_at=consent_confirmed_at,
            consent_text_version=consent_text_version,
            consent_capture_sources=consent_capture_sources,
            started_at=_parse_timestamp(document.get("started_at"), "started_at", optional=True),
            stopped_at=_parse_timestamp(document.get("stopped_at"), "stopped_at", optional=True),
        )
    except (TypeError, ValueError) as error:
        raise SessionDataError("Session document contains invalid values") from error


class SessionStore:
    """Persist versioned meeting-session documents with atomic replacement."""

    def __init__(self, root: Path):
        self.root = root

    def session_directory(self, session_id: str) -> Path:
        try:
            normalized_id = str(UUID(session_id))
        except ValueError as error:
            raise SessionDataError("session_id must be a UUID") from error
        return self.root / normalized_id

    def session_file(self, session_id: str) -> Path:
        return self.session_directory(session_id) / "session.json"

    def save(self, session: MeetingSession) -> Path:
        directory = self.session_directory(session.session_id)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "session.json"
        document = _to_document(session)

        descriptor, temporary_name = tempfile.mkstemp(
            prefix="session-",
            suffix=".tmp",
            dir=directory,
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)
        return destination

    def load(self, session_id: str) -> MeetingSession:
        path = self.session_file(session_id)
        if not path.is_file():
            raise SessionNotFoundError(f"Meeting session not found: {session_id}")
        try:
            raw_document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SessionDataError(f"Could not read meeting session: {session_id}") from error
        if not isinstance(raw_document, dict):
            raise SessionDataError("Session document must be a JSON object")
        document = cast(dict[str, object], raw_document)
        session = _from_document(document)
        if session.session_id != str(UUID(session_id)):
            raise SessionDataError("Session document ID does not match its directory")
        return session

    def list_sessions(self) -> list[MeetingSession]:
        if not self.root.is_dir():
            return []
        sessions = [
            self.load(path.parent.name)
            for path in self.root.glob("*/session.json")
            if path.is_file()
        ]
        return sorted(sessions, key=lambda session: session.updated_at, reverse=True)

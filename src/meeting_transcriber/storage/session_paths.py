from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from uuid import UUID


class SessionPathError(ValueError):
    """Raised when a meeting directory cannot be addressed unambiguously."""


def normalize_session_id(session_id: str) -> str:
    try:
        return str(UUID(session_id))
    except ValueError as error:
        raise SessionPathError("session_id must be a UUID") from error


def find_session_directory(meeting_root: Path, session_id: str) -> Path | None:
    """Find a meeting by the stable ID stored inside its session document."""

    normalized = normalize_session_id(session_id)
    legacy_directory = meeting_root / normalized
    if legacy_directory.is_dir():
        return legacy_directory
    if not meeting_root.is_dir():
        return None

    matches: list[Path] = []
    for session_file in meeting_root.glob("*/session.json"):
        if not session_file.is_file():
            continue
        try:
            document = json.loads(session_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(document, dict):
            continue
        document_id = document.get("session_id")
        if not isinstance(document_id, str):
            continue
        try:
            if normalize_session_id(document_id) == normalized:
                matches.append(session_file.parent)
        except SessionPathError:
            continue

    if len(matches) > 1:
        raise SessionPathError(f"Multiple meeting folders contain session ID {normalized}")
    return matches[0] if matches else None


def resolve_session_directory(meeting_root: Path, session_id: str) -> Path:
    """Resolve an existing readable folder, or the legacy path for a new artifact."""

    normalized = normalize_session_id(session_id)
    return find_session_directory(meeting_root, normalized) or meeting_root / normalized


def human_session_directory_name(title: str, created_at: datetime) -> str:
    """Create a readable, Windows-safe folder name from meeting metadata."""

    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise SessionPathError("Meeting creation timestamp must be timezone-aware")
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", title)
    safe_title = re.sub(r"\s+", " ", safe_title).strip(" .") or "Untitled meeting"
    safe_title = safe_title[:80].rstrip(" .")
    timestamp = created_at.astimezone().strftime("%Y-%m-%d %H%M%S")
    return f"{safe_title} - {timestamp}"


def allocate_session_directory(
    meeting_root: Path,
    title: str,
    created_at: datetime,
) -> Path:
    base_name = human_session_directory_name(title, created_at)
    candidate = meeting_root / base_name
    suffix = 2
    while candidate.exists():
        candidate = meeting_root / f"{base_name} ({suffix})"
        suffix += 1
    return candidate

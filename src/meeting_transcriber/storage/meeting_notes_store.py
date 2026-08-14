from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import UUID


class MeetingNotesDataError(ValueError):
    """Raised when a meeting-notes artifact cannot be safely addressed."""


class MeetingNotesStore:
    """Atomically persist readable TXT notes and retained rendered runs."""

    def __init__(self, meeting_root: Path):
        self.meeting_root = meeting_root

    def notes_file(self, session_id: str, run_id: str | None = None) -> Path:
        directory = self._session_directory(session_id)
        if run_id is not None:
            normalized_run = self._uuid(run_id, "run_id")
            return directory / "derived" / "meeting-notes" / f"{normalized_run}.txt"
        candidates = sorted(
            directory.glob("* - ????-??-?? ??????.txt"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        if candidates:
            return candidates[0]
        default_path = directory / "meeting-notes.txt"
        if default_path.is_file():
            return default_path
        legacy_path = directory / "meeting-notes.md"
        return legacy_path if legacy_path.is_file() else default_path

    def save(
        self,
        session_id: str,
        run_id: str,
        text: str,
        *,
        output_filename: str | None = None,
    ) -> Path:
        if not text.strip():
            raise MeetingNotesDataError("Meeting notes cannot be blank")
        normalized = text.rstrip() + "\n"
        retained_path = self.notes_file(session_id, run_id)
        self._save_text(retained_path, normalized)
        directory = self._session_directory(session_id)
        canonical_path = (
            directory / self._validated_output_filename(output_filename)
            if output_filename is not None
            else directory / "meeting-notes.txt"
        )
        self._save_text(canonical_path, normalized)
        return canonical_path

    def _session_directory(self, session_id: str) -> Path:
        return self.meeting_root / self._uuid(session_id, "session_id")

    @staticmethod
    def _uuid(value: str, field: str) -> str:
        try:
            return str(UUID(value))
        except ValueError as error:
            raise MeetingNotesDataError(f"{field} must be a UUID") from error

    @staticmethod
    def _validated_output_filename(value: str) -> str:
        path = Path(value)
        if path.name != value or path.suffix.casefold() != ".txt":
            raise MeetingNotesDataError("Output filename must be a safe TXT filename")
        return value

    @staticmethod
    def _save_text(path: Path, content: str) -> None:
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
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)


def meeting_notes_filename(title: str, recorded_at: datetime) -> str:
    """Create a readable, Windows-safe filename from a meeting title and local time."""

    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        raise MeetingNotesDataError("Recorded timestamp must be timezone-aware")
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", title)
    safe_title = re.sub(r"\s+", " ", safe_title).strip(" .") or "Untitled meeting"
    safe_title = safe_title[:80].rstrip(" .")
    timestamp = recorded_at.astimezone().strftime("%Y-%m-%d %H%M%S")
    return f"{safe_title} - {timestamp}.txt"

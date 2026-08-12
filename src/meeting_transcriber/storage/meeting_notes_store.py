from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import UUID


class MeetingNotesDataError(ValueError):
    """Raised when a meeting-notes artifact cannot be safely addressed."""


class MeetingNotesStore:
    """Atomically persist canonical Markdown notes and retained rendered runs."""

    def __init__(self, meeting_root: Path):
        self.meeting_root = meeting_root

    def notes_file(self, session_id: str, run_id: str | None = None) -> Path:
        directory = self._session_directory(session_id)
        if run_id is None:
            return directory / "meeting-notes.md"
        normalized_run = self._uuid(run_id, "run_id")
        return directory / "derived" / "meeting-notes" / f"{normalized_run}.md"

    def save(self, session_id: str, run_id: str, markdown: str) -> Path:
        if not markdown.strip():
            raise MeetingNotesDataError("Meeting notes cannot be blank")
        normalized = markdown.rstrip() + "\n"
        retained_path = self.notes_file(session_id, run_id)
        self._save_text(retained_path, normalized)
        canonical_path = self.notes_file(session_id)
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

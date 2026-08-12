from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


class FirstRunStore:
    """Persist whether the user has completed the local readiness walkthrough."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path):
        self.path = path

    def is_complete(self) -> bool:
        try:
            document: object = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return False
        return (
            isinstance(document, dict)
            and document.get("schema_version") == self.SCHEMA_VERSION
            and document.get("completed") is True
            and isinstance(document.get("completed_at"), str)
        )

    def mark_complete(self, *, at: datetime | None = None) -> None:
        timestamp = at or datetime.now(UTC)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("First-run completion time must be timezone-aware")
        document = {
            "schema_version": self.SCHEMA_VERSION,
            "completed": True,
            "completed_at": timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f"{self.path.stem}-",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

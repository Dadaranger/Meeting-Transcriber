from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class ApplicationSettingsStore:
    """Persist user-selected application paths without storing meeting content."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path):
        self.path = path

    def meetings_directory(self, default: Path) -> Path:
        try:
            document: object = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return default
        if not isinstance(document, dict) or document.get("schema_version") != self.SCHEMA_VERSION:
            return default
        value = document.get("meetings_directory")
        if not isinstance(value, str) or not value.strip():
            return default
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else default

    def set_meetings_directory(self, directory: Path) -> None:
        normalized = directory.expanduser()
        if not normalized.is_absolute():
            raise ValueError("Meeting storage folder must be an absolute path")
        document = {
            "schema_version": self.SCHEMA_VERSION,
            "meetings_directory": str(normalized),
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
                json.dump(document, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

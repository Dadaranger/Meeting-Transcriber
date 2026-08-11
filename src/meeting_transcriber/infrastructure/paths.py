from pathlib import Path

from PySide6.QtCore import QStandardPaths


def default_meetings_directory() -> Path:
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    if not documents:
        documents = str(Path.home() / "Documents")
    return Path(documents) / "Meeting Transcriber" / "Meetings"

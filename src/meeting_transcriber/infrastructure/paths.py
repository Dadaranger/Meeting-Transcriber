from pathlib import Path

from PySide6.QtCore import QStandardPaths


def default_meetings_directory() -> Path:
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    if not documents:
        documents = str(Path.home() / "Documents")
    return Path(documents) / "Meeting Transcriber" / "Meetings"


def default_models_directory() -> Path:
    application_data = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    if not application_data:
        application_data = str(Path.home() / ".meeting-transcriber")
    return Path(application_data) / "models"


def application_icon_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "meeting-transcriber.svg"

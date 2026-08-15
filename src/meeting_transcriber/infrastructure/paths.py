from pathlib import Path

from PySide6.QtCore import QStandardPaths


def default_meetings_directory() -> Path:
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    if not documents:
        documents = str(Path.home() / "Documents")
    return Path(documents) / "Meeting Transcriber" / "Meetings"


def default_models_directory() -> Path:
    return default_application_data_directory() / "models"


def default_application_data_directory() -> Path:
    application_data = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    if not application_data:
        application_data = str(Path.home() / ".meeting-transcriber")
    return Path(application_data)


def default_first_run_state_file() -> Path:
    return default_application_data_directory() / "first-run.json"


def default_application_settings_file() -> Path:
    return default_application_data_directory() / "settings.json"


def application_icon_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "meeting-transcriber.svg"

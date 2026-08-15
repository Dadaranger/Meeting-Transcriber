import json
from pathlib import Path

import pytest

from meeting_transcriber.storage.application_settings_store import ApplicationSettingsStore


def test_application_settings_persist_an_absolute_meeting_directory(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings" / "settings.json"
    store = ApplicationSettingsStore(settings_path)
    fallback = tmp_path / "fallback"
    selected = tmp_path / "meetings"

    assert store.meetings_directory(fallback) == fallback

    store.set_meetings_directory(selected)

    assert ApplicationSettingsStore(settings_path).meetings_directory(fallback) == selected
    document = json.loads(settings_path.read_text(encoding="utf-8"))
    assert document == {
        "schema_version": 1,
        "meetings_directory": str(selected),
    }


@pytest.mark.parametrize(
    "document",
    (
        "not json",
        '{"schema_version": 99, "meetings_directory": "C:/Meetings"}',
        '{"schema_version": 1, "meetings_directory": "relative/path"}',
    ),
)
def test_application_settings_fall_back_for_invalid_documents(
    tmp_path: Path,
    document: str,
) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(document, encoding="utf-8")
    fallback = tmp_path / "fallback"

    assert ApplicationSettingsStore(settings_path).meetings_directory(fallback) == fallback


def test_application_settings_reject_a_relative_directory(tmp_path: Path) -> None:
    store = ApplicationSettingsStore(tmp_path / "settings.json")

    with pytest.raises(ValueError, match="absolute"):
        store.set_meetings_directory(Path("relative/path"))

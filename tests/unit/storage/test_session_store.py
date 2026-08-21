import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meeting_transcriber.domain.session import (
    CONSENT_STATEMENT_VERSION,
    REQUIRED_CONSENT_SOURCES,
    MeetingSession,
    SessionOrigin,
)
from meeting_transcriber.storage.session_store import (
    SessionDataError,
    SessionNotFoundError,
    SessionStore,
    UnsupportedSessionSchema,
)

SESSION_ID = "f88b1560-77c2-4832-95da-33195619d52a"
START = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def test_session_round_trip_uses_versioned_json(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = MeetingSession.new("Design review", session_id=SESSION_ID, now=START)

    path = store.save(session)
    loaded = store.load(session.session_id)
    document = json.loads(path.read_text(encoding="utf-8"))

    assert loaded == session
    assert document["schema_version"] == MeetingSession.SCHEMA_VERSION
    assert document["state"] == "draft"
    assert document["origin"] == "live_recording"
    assert document["consent"] is None
    assert path.parent.name == f"Design review - {START.astimezone():%Y-%m-%d %H%M%S}"
    assert list(path.parent.glob("*.tmp")) == []


def test_confirmed_consent_round_trip_is_versioned_and_source_specific(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = MeetingSession.new("Design review", session_id=SESSION_ID, now=START)
    session = session.confirm_consent(at=START + timedelta(seconds=1))

    path = store.save(session)
    loaded = store.load(session.session_id)
    document = json.loads(path.read_text(encoding="utf-8"))

    assert loaded == session
    assert document["consent"] == {
        "confirmed_at": "2026-08-10T12:00:01Z",
        "text_version": CONSENT_STATEMENT_VERSION,
        "capture_sources": [source.value for source in REQUIRED_CONSENT_SOURCES],
    }


def test_version_one_session_loads_as_legacy_consent(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = MeetingSession.new(session_id=SESSION_ID, now=START)
    path = store.save(session)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = 1
    document.pop("consent")
    document["consent_confirmed_at"] = "2026-08-10T12:00:01Z"
    path.write_text(json.dumps(document), encoding="utf-8")

    loaded = store.load(SESSION_ID)

    assert loaded.consent_confirmed_at == START + timedelta(seconds=1)
    assert loaded.consent_text_version == 0
    assert loaded.consent_capture_sources == ()
    assert not loaded.has_current_recording_consent
    assert loaded.origin is SessionOrigin.LIVE_RECORDING


def test_version_two_session_defaults_to_live_recording_origin(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = MeetingSession.new(session_id=SESSION_ID, now=START)
    path = store.save(session)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = 2
    document.pop("origin")
    path.write_text(json.dumps(document), encoding="utf-8")

    assert store.load(SESSION_ID).origin is SessionOrigin.LIVE_RECORDING


def test_imported_session_round_trip_preserves_origin(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = MeetingSession.imported("Imported interview", session_id=SESSION_ID, now=START)

    path = store.save(session)

    assert store.load(SESSION_ID) == session
    assert json.loads(path.read_text(encoding="utf-8"))["origin"] == "imported_media"


def test_save_atomically_replaces_an_existing_revision(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = MeetingSession.new("Original", session_id=SESSION_ID, now=START)
    store.save(session)

    renamed = session.rename("Updated", at=START + timedelta(minutes=1))
    store.save(renamed)

    assert store.load(SESSION_ID) == renamed


def test_missing_session_has_a_specific_error(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)

    with pytest.raises(SessionNotFoundError, match=SESSION_ID):
        store.load(SESSION_ID)


def test_unknown_schema_is_rejected(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = MeetingSession.new(session_id=SESSION_ID, now=START)
    path = store.save(session)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = 999
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(UnsupportedSessionSchema, match="999"):
        store.load(SESSION_ID)


def test_session_id_cannot_escape_storage_root(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)

    with pytest.raises(SessionDataError, match="UUID"):
        store.load("../outside")


def test_list_sessions_returns_most_recent_first(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    older = MeetingSession.new(
        "Older",
        session_id="df307794-2195-42ed-9ee0-7061ea02d652",
        now=START,
    )
    newer = MeetingSession.new(
        "Newer",
        session_id="acaf37e4-4372-417f-b6ed-cbe6f5a116ce",
        now=START + timedelta(minutes=1),
    )
    store.save(older)
    store.save(newer)

    assert store.list_sessions() == [newer, older]


def test_session_folder_name_is_windows_safe_and_readable(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = MeetingSession.new(
        "  Project: Atlas / planning?  ",
        session_id=SESSION_ID,
        now=START,
    )

    path = store.save(session)

    assert path.parent.name == f"Project Atlas planning - {START.astimezone():%Y-%m-%d %H%M%S}"
    assert store.load(SESSION_ID) == session


def test_legacy_uuid_folder_is_migrated_with_all_contents(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = MeetingSession.new("Design review", session_id=SESSION_ID, now=START)
    readable_directory = store.save(session).parent
    legacy_directory = tmp_path / SESSION_ID
    readable_directory.rename(legacy_directory)
    audio_file = legacy_directory / "audio" / "microphone.wav"
    audio_file.parent.mkdir()
    audio_file.write_bytes(b"recording")

    migrations = store.migrate_legacy_directories()

    assert len(migrations) == 1
    source, destination = migrations[0]
    assert source == legacy_directory
    assert destination.name == f"Design review - {START.astimezone():%Y-%m-%d %H%M%S}"
    assert not legacy_directory.exists()
    assert (destination / "audio" / "microphone.wav").read_bytes() == b"recording"
    assert store.load(SESSION_ID) == session


def test_legacy_folder_migration_uses_a_suffix_for_name_collisions(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = MeetingSession.new("Design review", session_id=SESSION_ID, now=START)
    readable_directory = store.save(session).parent
    legacy_directory = tmp_path / SESSION_ID
    readable_directory.rename(legacy_directory)
    collision = tmp_path / f"Design review - {START.astimezone():%Y-%m-%d %H%M%S}"
    collision.mkdir()

    migrations = store.migrate_legacy_directories()

    assert migrations[0][1].name == (f"Design review - {START.astimezone():%Y-%m-%d %H%M%S} (2)")

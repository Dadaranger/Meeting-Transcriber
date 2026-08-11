import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meeting_transcriber.domain.session import (
    CONSENT_STATEMENT_VERSION,
    REQUIRED_CONSENT_SOURCES,
    MeetingSession,
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
    assert document["consent"] is None
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

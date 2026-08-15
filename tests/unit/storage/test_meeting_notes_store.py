from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from meeting_transcriber.storage.meeting_notes_store import (
    MeetingNotesDataError,
    MeetingNotesStore,
    meeting_notes_filename,
)

SESSION_ID = "0781afac-122c-465c-9852-ad8e7c6809d8"
FIRST_RUN = "4f51400b-d9a3-4681-b851-ece4d6b4d955"
SECOND_RUN = "f4759363-a21e-4c11-abd7-30bb82960683"


def test_notes_store_retains_runs_and_updates_the_canonical_file(tmp_path: Path) -> None:
    store = MeetingNotesStore(tmp_path)
    filename = "Weekly sync - 2026-08-13 160000.txt"

    canonical = store.save(SESSION_ID, FIRST_RUN, "First\n", output_filename=filename)
    first_retained = store.notes_file(SESSION_ID, FIRST_RUN)
    store.save(SESSION_ID, SECOND_RUN, "Second", output_filename=filename)

    assert canonical == tmp_path / SESSION_ID / filename
    assert store.notes_file(SESSION_ID) == canonical
    assert canonical.read_text(encoding="utf-8") == "Second\n"
    assert first_retained.read_text(encoding="utf-8") == "First\n"
    assert store.notes_file(SESSION_ID, SECOND_RUN).read_text(encoding="utf-8") == "Second\n"


def test_notes_filename_uses_safe_title_and_local_recording_time() -> None:
    recorded_at = datetime(2026, 8, 14, 2, 3, 4, tzinfo=UTC)

    filename = meeting_notes_filename(' Project: Atlas / "launch" ', recorded_at)

    expected_stamp = recorded_at.astimezone().strftime("%Y-%m-%d %H%M%S")
    assert filename == f"Project Atlas launch - {expected_stamp}.txt"


def test_notes_store_rejects_blank_content_and_unsafe_identifiers(tmp_path: Path) -> None:
    store = MeetingNotesStore(tmp_path)

    with pytest.raises(MeetingNotesDataError, match="cannot be blank"):
        store.save(SESSION_ID, FIRST_RUN, "  ")
    with pytest.raises(MeetingNotesDataError, match="session_id must be a UUID"):
        store.notes_file("../escape")
    with pytest.raises(MeetingNotesDataError, match="safe TXT filename"):
        store.save(SESSION_ID, FIRST_RUN, "notes", output_filename="../escape.txt")

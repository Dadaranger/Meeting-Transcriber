from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from meeting_transcriber.storage.first_run_store import FirstRunStore


def test_first_run_store_is_false_until_atomic_completion_is_saved(tmp_path: Path) -> None:
    store = FirstRunStore(tmp_path / "settings" / "first-run.json")

    assert not store.is_complete()
    store.mark_complete(at=datetime(2026, 8, 12, 6, 0, tzinfo=UTC))

    assert store.is_complete()
    content = store.path.read_text(encoding="utf-8")
    assert '"schema_version": 1' in content
    assert '"completed_at": "2026-08-12T06:00:00Z"' in content


def test_first_run_store_treats_malformed_state_as_incomplete(tmp_path: Path) -> None:
    store = FirstRunStore(tmp_path / "first-run.json")
    store.path.write_text("not json", encoding="utf-8")

    assert not store.is_complete()

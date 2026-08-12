from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from meeting_transcriber.domain.diarization import DiarizationDocument, DiarizationTurn
from meeting_transcriber.storage.diarization_store import DiarizationDataError, DiarizationStore

SESSION_ID = "1d7ee70c-7c04-4d17-903b-7b55152ea495"
RUN_ID = "66c62603-793c-4940-b76b-4b5355347144"


def _document() -> DiarizationDocument:
    return DiarizationDocument.new(
        SESSION_ID,
        RUN_ID,
        engine="pyannote.audio",
        model="community-1",
        created_at=datetime(2026, 8, 12, 6, 0, tzinfo=UTC),
        turns=(
            DiarizationTurn(0, 500, "remote-1"),
            DiarizationTurn(600, 1_000, "remote-2"),
        ),
    )


def test_store_persists_canonical_and_retained_diarization(tmp_path: Path) -> None:
    store = DiarizationStore(tmp_path)

    canonical = store.save(_document())

    assert canonical == tmp_path / SESSION_ID / "diarization.json"
    assert store.load(SESSION_ID) == _document()
    assert store.load(SESSION_ID, RUN_ID) == _document()


def test_store_rejects_malformed_diarization(tmp_path: Path) -> None:
    store = DiarizationStore(tmp_path)
    path = store.diarization_file(SESSION_ID)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")

    with pytest.raises(DiarizationDataError, match="Unsupported"):
        store.load(SESSION_ID)


def test_exclusive_diarization_rejects_overlapping_turns() -> None:
    with pytest.raises(ValueError, match="cannot overlap"):
        DiarizationDocument.new(
            SESSION_ID,
            RUN_ID,
            engine="fixture",
            model="fixture",
            turns=(
                DiarizationTurn(0, 700, "remote-1"),
                DiarizationTurn(600, 900, "remote-2"),
            ),
        )

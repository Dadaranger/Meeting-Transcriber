from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from meeting_transcriber.domain.review import TranscriptReview
from meeting_transcriber.domain.transcript import (
    TranscriptDocument,
    TranscriptionProfile,
    TranscriptSource,
    TranscriptSpeaker,
)
from meeting_transcriber.storage.review_store import ReviewDataError, ReviewStore

SESSION_ID = "59faac54-c10a-46c8-8d3c-76d0f0047261"
RUN_ID = "cc574fa7-4ab4-4443-889b-eb078ea6cb04"
SECOND_RUN_ID = "f2645e1e-5a33-41bb-b49b-e126ab6d43b8"
START = datetime(2026, 8, 12, 3, 0, tzinfo=UTC)


def _transcript(run_id: str = RUN_ID) -> TranscriptDocument:
    return TranscriptDocument.new(
        SESSION_ID,
        run_id=run_id,
        language="en",
        engine="test",
        model="medium",
        profile=TranscriptionProfile.BALANCED,
        created_at=START,
        speakers=(
            TranscriptSpeaker("local", "You", TranscriptSource.MICROPHONE),
            TranscriptSpeaker("remote", "Remote", TranscriptSource.SYSTEM_AUDIO),
        ),
        segments=(),
    )


def test_review_store_retains_each_revision_and_loads_current_review(tmp_path: Path) -> None:
    transcript = _transcript()
    review = TranscriptReview.new(transcript, at=START)
    first = review.rename_speaker(transcript, "remote", "Morgan", at=START)
    second = first.rename_speaker(transcript, "remote", "Casey", at=START)
    store = ReviewStore(tmp_path)

    canonical = store.save(first)
    store.save(second)

    assert canonical == tmp_path / SESSION_ID / "transcript-review.json"
    assert store.load(SESSION_ID) == second
    assert store.revision_file(first).is_file()
    assert store.revision_file(second).is_file()


def test_review_store_migrates_names_to_a_new_run(tmp_path: Path) -> None:
    first_transcript = _transcript()
    review = TranscriptReview.new(first_transcript, at=START)
    review = review.rename_speaker(first_transcript, "remote", "Morgan", at=START)
    store = ReviewStore(tmp_path)
    store.save(review)

    migrated = store.load_for_transcript(_transcript(SECOND_RUN_ID))

    assert migrated.run_id == SECOND_RUN_ID
    assert migrated.speaker_names[0].display_name == "Morgan"


def test_review_store_rejects_unsaved_empty_and_malformed_reviews(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path)
    transcript = _transcript()

    with pytest.raises(ReviewDataError, match="user or migrated"):
        store.save(TranscriptReview.new(transcript))

    path = store.review_file(SESSION_ID)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ReviewDataError, match="Unsupported"):
        store.load(SESSION_ID)

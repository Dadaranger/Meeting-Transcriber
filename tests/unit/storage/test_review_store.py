from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from meeting_transcriber.domain.review import TranscriptReview
from meeting_transcriber.domain.transcript import (
    TranscriptDocument,
    TranscriptionProfile,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
)
from meeting_transcriber.storage.review_store import ReviewDataError, ReviewStore

SESSION_ID = "59faac54-c10a-46c8-8d3c-76d0f0047261"
RUN_ID = "cc574fa7-4ab4-4443-889b-eb078ea6cb04"
SECOND_RUN_ID = "f2645e1e-5a33-41bb-b49b-e126ab6d43b8"
START = datetime(2026, 8, 12, 3, 0, tzinfo=UTC)
SEGMENT_ID = "788d92ba-a413-4239-90f3-4678ae62b9b0"


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
            TranscriptSpeaker("remote-2", "Remote 2", TranscriptSource.SYSTEM_AUDIO),
        ),
        segments=(
            TranscriptSegment(
                SEGMENT_ID,
                0,
                1_000,
                "remote",
                "Hello",
                TranscriptSource.SYSTEM_AUDIO,
            ),
        ),
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


def test_review_store_round_trips_assignments_and_loads_schema_one(tmp_path: Path) -> None:
    transcript = _transcript()
    assigned = TranscriptReview.new(transcript, at=START).assign_segment(
        transcript,
        SEGMENT_ID,
        "remote-2",
        at=START,
    )
    store = ReviewStore(tmp_path)
    store.save(assigned)

    loaded = store.load(SESSION_ID)

    assert loaded == assigned
    assert loaded.apply(transcript).segments[0].speaker_id == "remote-2"

    legacy = json.loads(store.review_file(SESSION_ID).read_text(encoding="utf-8"))
    legacy["schema_version"] = 1
    legacy.pop("segment_speakers")
    store.review_file(SESSION_ID).write_text(json.dumps(legacy), encoding="utf-8")
    assert store.load(SESSION_ID).segment_speakers == ()


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

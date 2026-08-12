from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meeting_transcriber.domain.review import TranscriptReview
from meeting_transcriber.domain.transcript import (
    TranscriptDocument,
    TranscriptionProfile,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
    TranscriptWord,
)

SESSION_ID = "59faac54-c10a-46c8-8d3c-76d0f0047261"
RUN_ID = "cc574fa7-4ab4-4443-889b-eb078ea6cb04"
SECOND_RUN_ID = "f2645e1e-5a33-41bb-b49b-e126ab6d43b8"
SEGMENT_ID = "788d92ba-a413-4239-90f3-4678ae62b9b0"
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
            TranscriptSpeaker("remote", "Remote speakers", TranscriptSource.SYSTEM_AUDIO),
            TranscriptSpeaker("remote-2", "Remote speaker 2", TranscriptSource.SYSTEM_AUDIO),
        ),
        segments=(
            TranscriptSegment(
                SEGMENT_ID,
                100,
                800,
                "remote",
                "Project at less",
                TranscriptSource.SYSTEM_AUDIO,
                words=(TranscriptWord("Project at less", 100, 800),),
            ),
        ),
    )


def test_review_applies_sparse_speaker_and_text_corrections() -> None:
    transcript = _transcript()
    review = TranscriptReview.new(transcript, at=START)
    review = review.rename_speaker(
        transcript,
        "remote",
        "Morgan",
        at=START + timedelta(seconds=1),
    )
    review = review.correct_segment(
        transcript,
        SEGMENT_ID,
        "Project Atlas",
        at=START + timedelta(seconds=2),
    )

    corrected = review.apply(transcript)

    assert review.revision == 2
    assert corrected.speakers[1].display_name == "Morgan"
    assert corrected.segments[0].text == "Project Atlas"
    assert corrected.segments[0].words == ()
    assert transcript.speakers[1].display_name == "Remote speakers"
    assert transcript.segments[0].text == "Project at less"


def test_review_removes_correction_when_restored_to_model_value() -> None:
    transcript = _transcript()
    review = TranscriptReview.new(transcript, at=START)
    review = review.rename_speaker(transcript, "remote", "Morgan")
    review = review.correct_segment(transcript, SEGMENT_ID, "Project Atlas")

    review = review.rename_speaker(transcript, "remote", "Remote speakers")
    review = review.correct_segment(transcript, SEGMENT_ID, "Project at less")

    assert review.revision == 4
    assert review.speaker_names == ()
    assert review.segment_texts == ()
    assert review.apply(transcript) == transcript


def test_review_reassigns_segment_within_source_and_can_restore_model_speaker() -> None:
    transcript = _transcript()
    review = TranscriptReview.new(transcript, at=START)

    assigned = review.assign_segment(transcript, SEGMENT_ID, "remote-2")

    assert assigned.revision == 1
    assert assigned.apply(transcript).segments[0].speaker_id == "remote-2"
    with pytest.raises(ValueError, match="its audio source"):
        assigned.assign_segment(transcript, SEGMENT_ID, "local")

    restored = assigned.assign_segment(transcript, SEGMENT_ID, "remote")
    assert restored.revision == 2
    assert restored.segment_speakers == ()
    assert restored.apply(transcript) == transcript


def test_new_transcript_run_migrates_names_but_not_stale_segment_text() -> None:
    first = _transcript()
    review = TranscriptReview.new(first, at=START)
    review = review.rename_speaker(first, "remote", "Morgan")
    review = review.correct_segment(first, SEGMENT_ID, "Project Atlas")
    review = review.assign_segment(first, SEGMENT_ID, "remote-2")
    second = _transcript(SECOND_RUN_ID)

    migrated = review.migrate_speaker_names(second, at=START + timedelta(minutes=1))
    corrected = migrated.apply(second)

    assert migrated.run_id == SECOND_RUN_ID
    assert migrated.revision == 1
    assert migrated.segment_texts == ()
    assert migrated.segment_speakers == ()
    assert corrected.speakers[1].display_name == "Morgan"
    assert corrected.segments[0].text == "Project at less"


def test_review_rejects_unknown_and_mismatched_targets() -> None:
    transcript = _transcript()
    review = TranscriptReview.new(transcript)

    with pytest.raises(ValueError, match="Unknown transcript speaker"):
        review.rename_speaker(transcript, "missing", "Name")
    with pytest.raises(ValueError, match="Unknown transcript segment"):
        review.correct_segment(transcript, SECOND_RUN_ID, "Correction")
    with pytest.raises(ValueError, match="run IDs do not match"):
        review.apply(_transcript(SECOND_RUN_ID))

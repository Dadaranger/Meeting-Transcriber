from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from meeting_transcriber.app.review_service import MeetingReviewService, ReviewWorkflowError
from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.domain.session import SessionState
from meeting_transcriber.domain.transcript import (
    TranscriptDocument,
    TranscriptionProfile,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
    TranscriptWord,
)
from meeting_transcriber.storage.meeting_notes_store import MeetingNotesStore
from meeting_transcriber.storage.review_store import ReviewStore
from meeting_transcriber.storage.session_store import SessionStore
from meeting_transcriber.storage.transcript_store import TranscriptStore

SEGMENT_ID = "dd7cf46a-c802-42ab-8708-7b775de693fb"


def _ready_review_service(
    tmp_path: Path,
) -> tuple[MeetingReviewService, str, TranscriptStore, ReviewStore, MeetingNotesStore]:
    sessions = MeetingSessionService(SessionStore(tmp_path))
    draft = sessions.create_draft("Review meeting")
    sessions.confirm_recording_consent(draft.session_id)
    sessions.transition_state(draft.session_id, SessionState.RECORDING)
    sessions.transition_state(draft.session_id, SessionState.RECORDED)
    sessions.transition_state(draft.session_id, SessionState.PROCESSING)
    sessions.transition_state(draft.session_id, SessionState.READY)
    transcripts = TranscriptStore(tmp_path)
    transcript = TranscriptDocument.new(
        draft.session_id,
        language="en",
        engine="test",
        model="medium",
        profile=TranscriptionProfile.BALANCED,
        created_at=datetime(2026, 8, 12, 4, 0, tzinfo=UTC),
        speakers=(
            TranscriptSpeaker("local", "You", TranscriptSource.MICROPHONE),
            TranscriptSpeaker("remote", "Remote speakers", TranscriptSource.SYSTEM_AUDIO),
        ),
        segments=(
            TranscriptSegment(
                SEGMENT_ID,
                0,
                1_000,
                "remote",
                "Project at less",
                TranscriptSource.SYSTEM_AUDIO,
                words=(TranscriptWord("Project at less", 0, 1_000),),
            ),
        ),
    )
    transcripts.save_transcript(transcript)
    reviews = ReviewStore(tmp_path)
    notes = MeetingNotesStore(tmp_path)
    service = MeetingReviewService(sessions, transcripts, reviews, notes)
    return service, draft.session_id, transcripts, reviews, notes


def test_review_service_persists_edits_and_regenerates_markdown(tmp_path: Path) -> None:
    service, session_id, _transcripts, reviews, notes = _ready_review_service(tmp_path)

    renamed = service.rename_speaker(session_id, "remote", "Morgan")
    corrected = service.correct_segment(session_id, SEGMENT_ID, "Project Atlas")

    assert renamed.review.revision == 1
    assert corrected.review.revision == 2
    assert corrected.reviewed_transcript.speakers[1].display_name == "Morgan"
    assert corrected.reviewed_transcript.segments[0].text == "Project Atlas"
    assert reviews.revision_file(renamed.review).is_file()
    assert reviews.revision_file(corrected.review).is_file()
    markdown = notes.notes_file(session_id).read_text(encoding="utf-8")
    assert "Morgan" in markdown
    assert "Project Atlas" in markdown
    assert "Project at less" not in markdown


def test_review_service_rejects_missing_transcript_and_blank_correction(tmp_path: Path) -> None:
    sessions = MeetingSessionService(SessionStore(tmp_path))
    draft = sessions.create_draft("No transcript")
    service = MeetingReviewService(
        sessions,
        TranscriptStore(tmp_path),
        ReviewStore(tmp_path),
        MeetingNotesStore(tmp_path),
    )

    with pytest.raises(ReviewWorkflowError, match="Transcript not found"):
        service.load(draft.session_id)

    ready_service, session_id, _transcripts, _reviews, _notes = _ready_review_service(
        tmp_path / "ready"
    )
    with pytest.raises(ReviewWorkflowError, match="cannot be blank"):
        ready_service.correct_segment(session_id, SEGMENT_ID, " ")

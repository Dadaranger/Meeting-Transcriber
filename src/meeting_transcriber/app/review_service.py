from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.domain.review import TranscriptReview
from meeting_transcriber.domain.transcript import TranscriptDocument
from meeting_transcriber.processing.markdown_export import render_meeting_notes
from meeting_transcriber.storage.meeting_notes_store import MeetingNotesStore
from meeting_transcriber.storage.review_store import ReviewDataError, ReviewStore
from meeting_transcriber.storage.transcript_store import (
    TranscriptDataError,
    TranscriptNotFoundError,
    TranscriptStore,
)


class ReviewWorkflowError(RuntimeError):
    """Raised when transcript review artifacts cannot be loaded or regenerated."""


@dataclass(frozen=True, slots=True)
class ReviewSnapshot:
    source_transcript: TranscriptDocument
    review: TranscriptReview
    reviewed_transcript: TranscriptDocument
    notes_path: Path


class MeetingReviewService:
    """Apply durable corrections and regenerate human-readable meeting notes."""

    def __init__(
        self,
        session_service: MeetingSessionService,
        transcript_store: TranscriptStore,
        review_store: ReviewStore,
        notes_store: MeetingNotesStore,
    ):
        self.session_service = session_service
        self.transcript_store = transcript_store
        self.review_store = review_store
        self.notes_store = notes_store

    def load(self, session_id: str) -> ReviewSnapshot:
        try:
            transcript = self.transcript_store.load_transcript(session_id)
            review = self.review_store.load_for_transcript(transcript)
            reviewed = review.apply(transcript)
        except (TranscriptNotFoundError, TranscriptDataError, ReviewDataError, ValueError) as error:
            raise ReviewWorkflowError(str(error)) from error
        return ReviewSnapshot(
            transcript,
            review,
            reviewed,
            self.notes_store.notes_file(session_id),
        )

    def rename_speaker(
        self,
        session_id: str,
        speaker_id: str,
        display_name: str,
    ) -> ReviewSnapshot:
        snapshot = self.load(session_id)
        try:
            review = snapshot.review.rename_speaker(
                snapshot.source_transcript,
                speaker_id,
                display_name,
            )
            return self._save_and_render(snapshot.source_transcript, review)
        except (OSError, ValueError) as error:
            raise ReviewWorkflowError(str(error)) from error

    def correct_segment(
        self,
        session_id: str,
        segment_id: str,
        text: str,
    ) -> ReviewSnapshot:
        snapshot = self.load(session_id)
        try:
            review = snapshot.review.correct_segment(
                snapshot.source_transcript,
                segment_id,
                text,
            )
            return self._save_and_render(snapshot.source_transcript, review)
        except (OSError, ValueError) as error:
            raise ReviewWorkflowError(str(error)) from error

    def _save_and_render(
        self,
        transcript: TranscriptDocument,
        review: TranscriptReview,
    ) -> ReviewSnapshot:
        if review.revision > 0:
            self.review_store.save(review)
        reviewed = review.apply(transcript)
        session = self.session_service.get_session(transcript.session_id)
        notes_path = self.notes_store.save(
            transcript.session_id,
            transcript.run_id,
            render_meeting_notes(session, reviewed),
        )
        return ReviewSnapshot(transcript, review, reviewed, notes_path)

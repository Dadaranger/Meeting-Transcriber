from datetime import UTC, datetime
from pathlib import Path

from meeting_transcriber.domain.session import MeetingSession
from meeting_transcriber.storage.diarization_store import DiarizationStore
from meeting_transcriber.storage.meeting_notes_store import MeetingNotesStore
from meeting_transcriber.storage.review_store import ReviewStore
from meeting_transcriber.storage.session_store import SessionStore
from meeting_transcriber.storage.transcript_store import TranscriptStore

SESSION_ID = "f88b1560-77c2-4832-95da-33195619d52a"


def test_every_artifact_store_resolves_the_readable_meeting_directory(tmp_path: Path) -> None:
    session_store = SessionStore(tmp_path)
    session = MeetingSession.new(
        "Customer interview",
        session_id=SESSION_ID,
        now=datetime(2026, 8, 15, 20, 30, tzinfo=UTC),
    )
    session_directory = session_store.save(session).parent

    artifact_paths = (
        TranscriptStore(tmp_path).job_file(SESSION_ID),
        ReviewStore(tmp_path).review_file(SESSION_ID),
        MeetingNotesStore(tmp_path).notes_file(SESSION_ID),
        DiarizationStore(tmp_path).diarization_file(SESSION_ID),
    )

    assert session_directory.name.startswith("Customer interview - ")
    assert all(path.is_relative_to(session_directory) for path in artifact_paths)

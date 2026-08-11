from pathlib import Path

from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.domain.session import (
    CONSENT_STATEMENT_VERSION,
    REQUIRED_CONSENT_SOURCES,
)
from meeting_transcriber.storage.session_store import SessionStore


def test_service_persists_current_recording_consent(tmp_path: Path) -> None:
    service = MeetingSessionService(SessionStore(tmp_path))
    draft = service.create_draft("Weekly sync")

    confirmed = service.confirm_recording_consent(draft.session_id)

    assert confirmed.has_current_recording_consent
    assert confirmed.consent_text_version == CONSENT_STATEMENT_VERSION
    assert confirmed.consent_capture_sources == REQUIRED_CONSENT_SOURCES
    assert service.get_session(draft.session_id) == confirmed

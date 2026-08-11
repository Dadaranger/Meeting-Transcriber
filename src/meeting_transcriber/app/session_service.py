from __future__ import annotations

from dataclasses import dataclass

from meeting_transcriber.domain.session import (
    REQUIRED_CONSENT_SOURCES,
    ConsentCaptureSource,
    MeetingSession,
    SessionState,
)
from meeting_transcriber.storage.session_store import SessionStore


@dataclass(slots=True)
class MeetingSessionService:
    """Application operations for creating and retrieving meeting sessions."""

    store: SessionStore

    def create_draft(self, title: str = "Untitled meeting") -> MeetingSession:
        session = MeetingSession.new(title)
        self.store.save(session)
        return session

    def get_session(self, session_id: str) -> MeetingSession:
        return self.store.load(session_id)

    def confirm_recording_consent(
        self,
        session_id: str,
        capture_sources: tuple[ConsentCaptureSource, ...] = REQUIRED_CONSENT_SOURCES,
    ) -> MeetingSession:
        session = self.store.load(session_id)
        confirmed = session.confirm_consent(capture_sources)
        self.store.save(confirmed)
        return confirmed

    def transition_state(self, session_id: str, target: SessionState) -> MeetingSession:
        session = self.store.load(session_id)
        transitioned = session.transition(target)
        self.store.save(transitioned)
        return transitioned

    def recent_sessions(self) -> list[MeetingSession]:
        return self.store.list_sessions()

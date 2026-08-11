from __future__ import annotations

from dataclasses import dataclass

from meeting_transcriber.domain.session import MeetingSession
from meeting_transcriber.storage.session_store import SessionStore


@dataclass(slots=True)
class MeetingSessionService:
    """Application operations for creating and retrieving meeting sessions."""

    store: SessionStore

    def create_draft(self, title: str = "Untitled meeting") -> MeetingSession:
        session = MeetingSession.new(title)
        self.store.save(session)
        return session

    def recent_sessions(self) -> list[MeetingSession]:
        return self.store.list_sessions()

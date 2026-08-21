from __future__ import annotations

import json
import wave
from dataclasses import dataclass
from pathlib import Path

from meeting_transcriber.domain.session import (
    REQUIRED_CONSENT_SOURCES,
    ConsentCaptureSource,
    MeetingSession,
    SessionState,
)
from meeting_transcriber.storage.session_store import SessionStore


class SessionRecoveryError(RuntimeError):
    """Raised when an interrupted session has no recoverable capture artifacts."""


@dataclass(slots=True)
class MeetingSessionService:
    """Application operations for creating and retrieving meeting sessions."""

    store: SessionStore

    def create_draft(self, title: str = "Untitled meeting") -> MeetingSession:
        session = MeetingSession.new(title)
        self.store.save(session)
        return session

    def create_imported(self, title: str = "Imported recording") -> MeetingSession:
        session = MeetingSession.imported(title)
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

    def recover_abandoned_recordings(self) -> list[MeetingSession]:
        recovered: list[MeetingSession] = []
        for session in self.store.list_sessions():
            if session.state not in {SessionState.RECORDING, SessionState.PAUSED}:
                continue
            interrupted = session.transition(SessionState.INTERRUPTED)
            self.store.save(interrupted)
            recovered.append(interrupted)
        return recovered

    def has_recoverable_audio(self, session_id: str) -> bool:
        directory = self.store.session_directory(session_id)
        manifest_path = directory / "capture.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(manifest, dict) or manifest.get("session_id") != session_id:
            return False
        return any(self._is_finalized_wav(path) for path in (directory / "audio").glob("*.wav"))

    def recover_interrupted_session(self, session_id: str) -> MeetingSession:
        session = self.store.load(session_id)
        if session.state is not SessionState.INTERRUPTED:
            raise SessionRecoveryError("Only interrupted sessions can be recovered")
        if not self.has_recoverable_audio(session_id):
            raise SessionRecoveryError("No finalized audio chunks are available for recovery")
        recovered = session.transition(SessionState.RECORDED)
        self.store.save(recovered)
        return recovered

    def session_directory(self, session_id: str) -> Path:
        return self.store.session_directory(session_id)

    @staticmethod
    def _is_finalized_wav(path: Path) -> bool:
        try:
            with wave.open(str(path), "rb") as wav_file:
                return wav_file.getnframes() > 0
        except (OSError, EOFError, wave.Error):
            return False

    def recent_sessions(self) -> list[MeetingSession]:
        return self.store.list_sessions()

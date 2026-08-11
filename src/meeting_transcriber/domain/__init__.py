"""Core domain models with no UI or platform dependencies."""

from meeting_transcriber.domain.session import (
    CONSENT_STATEMENT,
    CONSENT_STATEMENT_VERSION,
    ConsentCaptureSource,
    InvalidSessionTransition,
    MeetingSession,
    SessionState,
)

__all__ = [
    "CONSENT_STATEMENT",
    "CONSENT_STATEMENT_VERSION",
    "ConsentCaptureSource",
    "InvalidSessionTransition",
    "MeetingSession",
    "SessionState",
]

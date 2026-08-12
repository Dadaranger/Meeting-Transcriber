"""Core domain models with no UI or platform dependencies."""

from meeting_transcriber.domain.session import (
    CONSENT_STATEMENT,
    CONSENT_STATEMENT_VERSION,
    ConsentCaptureSource,
    InvalidSessionTransition,
    MeetingSession,
    SessionState,
)
from meeting_transcriber.domain.transcript import (
    InvalidTranscriptionJobTransition,
    TranscriptDocument,
    TranscriptionJob,
    TranscriptionJobState,
    TranscriptionProfile,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
    TranscriptWord,
)

__all__ = [
    "CONSENT_STATEMENT",
    "CONSENT_STATEMENT_VERSION",
    "ConsentCaptureSource",
    "InvalidSessionTransition",
    "InvalidTranscriptionJobTransition",
    "MeetingSession",
    "SessionState",
    "TranscriptDocument",
    "TranscriptSegment",
    "TranscriptSource",
    "TranscriptSpeaker",
    "TranscriptWord",
    "TranscriptionJob",
    "TranscriptionJobState",
    "TranscriptionProfile",
]

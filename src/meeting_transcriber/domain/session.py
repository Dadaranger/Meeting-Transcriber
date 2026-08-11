from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar, Final
from uuid import UUID, uuid4


class SessionState(StrEnum):
    DRAFT = "draft"
    RECORDING = "recording"
    PAUSED = "paused"
    RECORDED = "recorded"
    INTERRUPTED = "interrupted"
    PROCESSING = "processing"
    READY = "ready"
    EXPORTED = "exported"


ALLOWED_TRANSITIONS: Final[dict[SessionState, frozenset[SessionState]]] = {
    SessionState.DRAFT: frozenset({SessionState.RECORDING}),
    SessionState.RECORDING: frozenset(
        {SessionState.PAUSED, SessionState.RECORDED, SessionState.INTERRUPTED}
    ),
    SessionState.PAUSED: frozenset(
        {SessionState.RECORDING, SessionState.RECORDED, SessionState.INTERRUPTED}
    ),
    SessionState.INTERRUPTED: frozenset({SessionState.RECORDED}),
    SessionState.RECORDED: frozenset({SessionState.PROCESSING}),
    SessionState.PROCESSING: frozenset({SessionState.READY, SessionState.RECORDED}),
    SessionState.READY: frozenset({SessionState.PROCESSING, SessionState.EXPORTED}),
    SessionState.EXPORTED: frozenset({SessionState.PROCESSING}),
}


class InvalidSessionTransition(ValueError):
    """Raised when a meeting session cannot move to the requested state."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def _validated_timestamp(value: datetime | None) -> datetime:
    timestamp = value or utc_now()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Session timestamps must be timezone-aware")
    return timestamp.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class MeetingSession:
    """Immutable lifecycle state for one meeting session."""

    SCHEMA_VERSION: ClassVar[int] = 1

    session_id: str
    title: str
    state: SessionState
    created_at: datetime
    updated_at: datetime
    revision: int = 0
    consent_confirmed_at: datetime | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None

    def __post_init__(self) -> None:
        UUID(self.session_id)
        if not self.title.strip():
            raise ValueError("Meeting title cannot be blank")
        if self.revision < 0:
            raise ValueError("Session revision cannot be negative")
        for timestamp in (
            self.created_at,
            self.updated_at,
            self.consent_confirmed_at,
            self.started_at,
            self.stopped_at,
        ):
            if timestamp is not None and (
                timestamp.tzinfo is None or timestamp.utcoffset() is None
            ):
                raise ValueError("Session timestamps must be timezone-aware")

    @classmethod
    def new(
        cls,
        title: str = "Untitled meeting",
        *,
        session_id: str | None = None,
        now: datetime | None = None,
    ) -> MeetingSession:
        timestamp = _validated_timestamp(now)
        normalized_title = title.strip() or "Untitled meeting"
        return cls(
            session_id=session_id or str(uuid4()),
            title=normalized_title,
            state=SessionState.DRAFT,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def confirm_consent(self, *, at: datetime | None = None) -> MeetingSession:
        if self.state is not SessionState.DRAFT:
            raise InvalidSessionTransition("Consent can only be confirmed for a draft session")
        if self.consent_confirmed_at is not None:
            return self
        timestamp = _validated_timestamp(at)
        return replace(
            self,
            consent_confirmed_at=timestamp,
            updated_at=timestamp,
            revision=self.revision + 1,
        )

    def rename(self, title: str, *, at: datetime | None = None) -> MeetingSession:
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("Meeting title cannot be blank")
        if normalized_title == self.title:
            return self
        timestamp = _validated_timestamp(at)
        return replace(
            self,
            title=normalized_title,
            updated_at=timestamp,
            revision=self.revision + 1,
        )

    def transition(
        self,
        target: SessionState,
        *,
        at: datetime | None = None,
    ) -> MeetingSession:
        if target not in ALLOWED_TRANSITIONS[self.state]:
            raise InvalidSessionTransition(
                f"Cannot transition meeting session from {self.state.value} to {target.value}"
            )
        if (
            self.state is SessionState.DRAFT
            and target is SessionState.RECORDING
            and self.consent_confirmed_at is None
        ):
            raise InvalidSessionTransition("Consent must be confirmed before recording")

        timestamp = _validated_timestamp(at)
        started_at = self.started_at
        stopped_at = self.stopped_at
        if self.state is SessionState.DRAFT and target is SessionState.RECORDING:
            started_at = timestamp
        if target in {SessionState.RECORDED, SessionState.INTERRUPTED}:
            stopped_at = self.stopped_at or timestamp
        return replace(
            self,
            state=target,
            updated_at=timestamp,
            revision=self.revision + 1,
            started_at=started_at,
            stopped_at=stopped_at,
        )

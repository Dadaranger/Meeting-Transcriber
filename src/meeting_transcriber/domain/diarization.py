from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID


def _timestamp(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Diarization timestamps must be timezone-aware")
    return timestamp.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class DiarizationTurn:
    start_ms: int
    end_ms: int
    speaker_id: str

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("Diarization turns must have positive duration")
        if not self.speaker_id.strip():
            raise ValueError("Diarization speaker ID cannot be blank")


@dataclass(frozen=True, slots=True)
class DiarizationDocument:
    """Exclusive remote-speaker turns for one transcription run."""

    SCHEMA_VERSION: ClassVar[int] = 1

    session_id: str
    run_id: str
    engine: str
    model: str
    created_at: datetime
    turns: tuple[DiarizationTurn, ...]

    def __post_init__(self) -> None:
        UUID(self.session_id)
        UUID(self.run_id)
        if not self.engine.strip() or not self.model.strip():
            raise ValueError("Diarization engine and model are required")
        _timestamp(self.created_at)
        expected_order = tuple(
            sorted(self.turns, key=lambda turn: (turn.start_ms, turn.end_ms, turn.speaker_id))
        )
        if self.turns != expected_order:
            raise ValueError("Diarization turns must use deterministic chronological ordering")
        previous_end = 0
        for turn in self.turns:
            if turn.start_ms < previous_end:
                raise ValueError("Exclusive diarization turns cannot overlap")
            previous_end = turn.end_ms

    @classmethod
    def new(
        cls,
        session_id: str,
        run_id: str,
        *,
        engine: str,
        model: str,
        turns: tuple[DiarizationTurn, ...],
        created_at: datetime | None = None,
    ) -> DiarizationDocument:
        return cls(
            session_id,
            run_id,
            engine.strip(),
            model.strip(),
            _timestamp(created_at),
            turns,
        )

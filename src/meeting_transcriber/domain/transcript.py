from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar, Final
from uuid import UUID, uuid4


class TranscriptSource(StrEnum):
    MICROPHONE = "microphone"
    SYSTEM_AUDIO = "system_audio"


class TranscriptionProfile(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    ACCURATE = "accurate"


class TranscriptionJobState(StrEnum):
    PENDING = "pending"
    PREPARING = "preparing"
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


ALLOWED_JOB_TRANSITIONS: Final[dict[TranscriptionJobState, frozenset[TranscriptionJobState]]] = {
    TranscriptionJobState.PENDING: frozenset(
        {TranscriptionJobState.PREPARING, TranscriptionJobState.CANCELLED}
    ),
    TranscriptionJobState.PREPARING: frozenset(
        {
            TranscriptionJobState.TRANSCRIBING,
            TranscriptionJobState.CANCELLED,
            TranscriptionJobState.FAILED,
        }
    ),
    TranscriptionJobState.TRANSCRIBING: frozenset(
        {
            TranscriptionJobState.COMPLETED,
            TranscriptionJobState.CANCELLED,
            TranscriptionJobState.FAILED,
        }
    ),
    TranscriptionJobState.COMPLETED: frozenset(),
    TranscriptionJobState.CANCELLED: frozenset({TranscriptionJobState.PENDING}),
    TranscriptionJobState.FAILED: frozenset({TranscriptionJobState.PENDING}),
}


class InvalidTranscriptionJobTransition(ValueError):
    """Raised when persisted transcription work cannot change to a requested state."""


def _utc_timestamp(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Transcript timestamps must be timezone-aware")
    return timestamp.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    text: str
    start_ms: int
    end_ms: int
    probability: float | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Transcript word text cannot be blank")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("Transcript word timestamps must have positive duration")
        if self.probability is not None and not 0.0 <= self.probability <= 1.0:
            raise ValueError("Transcript word probability must be between zero and one")


@dataclass(frozen=True, slots=True)
class TranscriptSpeaker:
    speaker_id: str
    display_name: str
    source: TranscriptSource

    def __post_init__(self) -> None:
        if not self.speaker_id.strip():
            raise ValueError("Transcript speaker ID cannot be blank")
        if not self.display_name.strip():
            raise ValueError("Transcript speaker name cannot be blank")


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    segment_id: str
    start_ms: int
    end_ms: int
    speaker_id: str
    text: str
    source: TranscriptSource
    confidence: float | None = None
    words: tuple[TranscriptWord, ...] = ()

    def __post_init__(self) -> None:
        UUID(self.segment_id)
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("Transcript segment timestamps must have positive duration")
        if not self.speaker_id.strip() or not self.text.strip():
            raise ValueError("Transcript segment speaker and text cannot be blank")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Transcript segment confidence must be between zero and one")
        previous_end = self.start_ms
        for word in self.words:
            if word.start_ms < self.start_ms or word.end_ms > self.end_ms:
                raise ValueError("Transcript word timestamps must stay within their segment")
            if word.start_ms < previous_end:
                raise ValueError("Transcript words must be ordered without overlap")
            previous_end = word.end_ms


@dataclass(frozen=True, slots=True)
class TranscriptDocument:
    SCHEMA_VERSION: ClassVar[int] = 1

    session_id: str
    run_id: str
    language: str
    engine: str
    model: str
    profile: TranscriptionProfile
    created_at: datetime
    speakers: tuple[TranscriptSpeaker, ...]
    segments: tuple[TranscriptSegment, ...]

    def __post_init__(self) -> None:
        UUID(self.session_id)
        UUID(self.run_id)
        if not self.language.strip() or not self.engine.strip() or not self.model.strip():
            raise ValueError("Transcript language, engine, and model are required")
        _utc_timestamp(self.created_at)
        speaker_ids = [speaker.speaker_id for speaker in self.speakers]
        if len(speaker_ids) != len(set(speaker_ids)):
            raise ValueError("Transcript speaker IDs must be unique")
        speakers_by_id = {speaker.speaker_id: speaker for speaker in self.speakers}
        expected_order = tuple(
            sorted(
                self.segments,
                key=lambda segment: (segment.start_ms, segment.end_ms, segment.segment_id),
            )
        )
        if self.segments != expected_order:
            raise ValueError("Transcript segments must use deterministic chronological ordering")
        for segment in self.segments:
            speaker = speakers_by_id.get(segment.speaker_id)
            if speaker is None:
                raise ValueError("Transcript segment references an unknown speaker")
            if speaker.source is not segment.source:
                raise ValueError("Transcript segment source must match its speaker source")

    @classmethod
    def new(
        cls,
        session_id: str,
        *,
        language: str,
        engine: str,
        model: str,
        profile: TranscriptionProfile,
        speakers: tuple[TranscriptSpeaker, ...],
        segments: tuple[TranscriptSegment, ...],
        run_id: str | None = None,
        created_at: datetime | None = None,
    ) -> TranscriptDocument:
        return cls(
            session_id=session_id,
            run_id=run_id or str(uuid4()),
            language=language.strip(),
            engine=engine.strip(),
            model=model.strip(),
            profile=profile,
            created_at=_utc_timestamp(created_at),
            speakers=speakers,
            segments=segments,
        )

    @property
    def duration_ms(self) -> int:
        return max((segment.end_ms for segment in self.segments), default=0)


@dataclass(frozen=True, slots=True)
class TranscriptionJob:
    SCHEMA_VERSION: ClassVar[int] = 1

    job_id: str
    session_id: str
    state: TranscriptionJobState
    profile: TranscriptionProfile
    language: str | None
    created_at: datetime
    updated_at: datetime
    attempt: int = 1
    processed_audio_ms: int = 0
    total_audio_ms: int = 0
    error: str | None = None

    def __post_init__(self) -> None:
        UUID(self.job_id)
        UUID(self.session_id)
        _utc_timestamp(self.created_at)
        _utc_timestamp(self.updated_at)
        if self.language is not None and not self.language.strip():
            raise ValueError("Requested transcription language cannot be blank")
        if self.attempt < 1:
            raise ValueError("Transcription attempt must be positive")
        if self.processed_audio_ms < 0 or self.total_audio_ms < 0:
            raise ValueError("Transcription progress cannot be negative")
        if self.total_audio_ms and self.processed_audio_ms > self.total_audio_ms:
            raise ValueError("Processed transcription audio cannot exceed the total")
        if self.state is TranscriptionJobState.FAILED and not self.error:
            raise ValueError("Failed transcription jobs require an error")
        if self.state is not TranscriptionJobState.FAILED and self.error is not None:
            raise ValueError("Only failed transcription jobs may retain an error")

    @classmethod
    def new(
        cls,
        session_id: str,
        *,
        profile: TranscriptionProfile = TranscriptionProfile.BALANCED,
        language: str | None = None,
        job_id: str | None = None,
        created_at: datetime | None = None,
    ) -> TranscriptionJob:
        timestamp = _utc_timestamp(created_at)
        return cls(
            job_id=job_id or str(uuid4()),
            session_id=session_id,
            state=TranscriptionJobState.PENDING,
            profile=profile,
            language=language.strip() if language is not None else None,
            created_at=timestamp,
            updated_at=timestamp,
        )

    @property
    def progress(self) -> float:
        if self.state is TranscriptionJobState.COMPLETED:
            return 1.0
        if self.total_audio_ms == 0:
            return 0.0
        return self.processed_audio_ms / self.total_audio_ms

    def transition(
        self,
        target: TranscriptionJobState,
        *,
        at: datetime | None = None,
        error: str | None = None,
    ) -> TranscriptionJob:
        if target not in ALLOWED_JOB_TRANSITIONS[self.state]:
            raise InvalidTranscriptionJobTransition(
                f"Cannot transition transcription job from {self.state.value} to {target.value}"
            )
        if target is TranscriptionJobState.FAILED and not error:
            raise ValueError("Failed transcription jobs require an error")
        return replace(
            self,
            state=target,
            updated_at=_utc_timestamp(at),
            error=error if target is TranscriptionJobState.FAILED else None,
            processed_audio_ms=(
                self.total_audio_ms
                if target is TranscriptionJobState.COMPLETED
                else self.processed_audio_ms
            ),
        )

    def with_progress(
        self,
        processed_audio_ms: int,
        total_audio_ms: int,
        *,
        at: datetime | None = None,
    ) -> TranscriptionJob:
        if self.state not in {
            TranscriptionJobState.PREPARING,
            TranscriptionJobState.TRANSCRIBING,
        }:
            raise InvalidTranscriptionJobTransition(
                "Transcription progress requires a preparing or transcribing job"
            )
        return replace(
            self,
            processed_audio_ms=processed_audio_ms,
            total_audio_ms=total_audio_ms,
            updated_at=_utc_timestamp(at),
        )

    def retry(self, *, at: datetime | None = None) -> TranscriptionJob:
        if TranscriptionJobState.PENDING not in ALLOWED_JOB_TRANSITIONS[self.state]:
            raise InvalidTranscriptionJobTransition("Only cancelled or failed jobs can retry")
        return replace(
            self,
            state=TranscriptionJobState.PENDING,
            updated_at=_utc_timestamp(at),
            attempt=self.attempt + 1,
            processed_audio_ms=0,
            total_audio_ms=0,
            error=None,
        )

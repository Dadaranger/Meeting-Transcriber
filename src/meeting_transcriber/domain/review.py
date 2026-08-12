from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID

from meeting_transcriber.domain.transcript import TranscriptDocument


def _timestamp(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Review timestamps must be timezone-aware")
    return timestamp.astimezone(UTC)


@dataclass(frozen=True, slots=True, order=True)
class SpeakerNameCorrection:
    speaker_id: str
    display_name: str

    def __post_init__(self) -> None:
        if not self.speaker_id.strip() or not self.display_name.strip():
            raise ValueError("Speaker correction ID and display name cannot be blank")


@dataclass(frozen=True, slots=True, order=True)
class SegmentTextCorrection:
    segment_id: str
    text: str

    def __post_init__(self) -> None:
        UUID(self.segment_id)
        if not self.text.strip():
            raise ValueError("Corrected segment text cannot be blank")


@dataclass(frozen=True, slots=True)
class TranscriptReview:
    """Sparse, versioned user corrections for exactly one transcript run."""

    SCHEMA_VERSION: ClassVar[int] = 1

    session_id: str
    run_id: str
    revision: int
    updated_at: datetime
    speaker_names: tuple[SpeakerNameCorrection, ...] = ()
    segment_texts: tuple[SegmentTextCorrection, ...] = ()

    def __post_init__(self) -> None:
        UUID(self.session_id)
        UUID(self.run_id)
        if self.revision < 0:
            raise ValueError("Review revision cannot be negative")
        _timestamp(self.updated_at)
        if self.speaker_names != tuple(sorted(self.speaker_names)):
            raise ValueError("Speaker corrections must use deterministic ordering")
        if self.segment_texts != tuple(sorted(self.segment_texts)):
            raise ValueError("Segment corrections must use deterministic ordering")
        speaker_ids = [correction.speaker_id for correction in self.speaker_names]
        segment_ids = [correction.segment_id for correction in self.segment_texts]
        if len(speaker_ids) != len(set(speaker_ids)):
            raise ValueError("A review cannot correct one speaker more than once")
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("A review cannot correct one segment more than once")

    @classmethod
    def new(
        cls,
        transcript: TranscriptDocument,
        *,
        at: datetime | None = None,
    ) -> TranscriptReview:
        return cls(
            transcript.session_id,
            transcript.run_id,
            0,
            _timestamp(at),
        )

    def apply(self, transcript: TranscriptDocument) -> TranscriptDocument:
        self._validate_transcript(transcript)
        names = {
            correction.speaker_id: correction.display_name for correction in self.speaker_names
        }
        texts = {correction.segment_id: correction.text for correction in self.segment_texts}
        known_speakers = {speaker.speaker_id for speaker in transcript.speakers}
        known_segments = {segment.segment_id for segment in transcript.segments}
        unknown_speakers = names.keys() - known_speakers
        unknown_segments = texts.keys() - known_segments
        if unknown_speakers:
            raise ValueError(f"Review references unknown speaker {min(unknown_speakers)!r}")
        if unknown_segments:
            raise ValueError(f"Review references unknown segment {min(unknown_segments)!r}")
        return replace(
            transcript,
            speakers=tuple(
                replace(speaker, display_name=names.get(speaker.speaker_id, speaker.display_name))
                for speaker in transcript.speakers
            ),
            segments=tuple(
                replace(
                    segment,
                    text=texts.get(segment.segment_id, segment.text),
                    words=() if segment.segment_id in texts else segment.words,
                )
                for segment in transcript.segments
            ),
        )

    def rename_speaker(
        self,
        transcript: TranscriptDocument,
        speaker_id: str,
        display_name: str,
        *,
        at: datetime | None = None,
    ) -> TranscriptReview:
        self._validate_transcript(transcript)
        speaker = next(
            (candidate for candidate in transcript.speakers if candidate.speaker_id == speaker_id),
            None,
        )
        if speaker is None:
            raise ValueError(f"Unknown transcript speaker {speaker_id!r}")
        normalized = display_name.strip()
        if not normalized:
            raise ValueError("Speaker display name cannot be blank")
        corrections = {
            correction.speaker_id: correction.display_name for correction in self.speaker_names
        }
        if normalized == speaker.display_name:
            corrections.pop(speaker_id, None)
        else:
            corrections[speaker_id] = normalized
        updated = tuple(
            sorted(
                SpeakerNameCorrection(correction_id, name)
                for correction_id, name in corrections.items()
            )
        )
        if updated == self.speaker_names:
            return self
        return replace(
            self,
            revision=self.revision + 1,
            updated_at=_timestamp(at),
            speaker_names=updated,
        )

    def correct_segment(
        self,
        transcript: TranscriptDocument,
        segment_id: str,
        text: str,
        *,
        at: datetime | None = None,
    ) -> TranscriptReview:
        self._validate_transcript(transcript)
        segment = next(
            (candidate for candidate in transcript.segments if candidate.segment_id == segment_id),
            None,
        )
        if segment is None:
            raise ValueError(f"Unknown transcript segment {segment_id!r}")
        normalized = text.strip()
        if not normalized:
            raise ValueError("Corrected segment text cannot be blank")
        corrections = {correction.segment_id: correction.text for correction in self.segment_texts}
        if normalized == segment.text:
            corrections.pop(segment_id, None)
        else:
            corrections[segment_id] = normalized
        updated = tuple(
            sorted(
                SegmentTextCorrection(correction_id, corrected_text)
                for correction_id, corrected_text in corrections.items()
            )
        )
        if updated == self.segment_texts:
            return self
        return replace(
            self,
            revision=self.revision + 1,
            updated_at=_timestamp(at),
            segment_texts=updated,
        )

    def migrate_speaker_names(
        self,
        transcript: TranscriptDocument,
        *,
        at: datetime | None = None,
    ) -> TranscriptReview:
        """Start a new-run review with stable speaker names but no stale text edits."""

        if transcript.session_id != self.session_id:
            raise ValueError("Cannot migrate a review to a different meeting session")
        migrated = TranscriptReview.new(transcript, at=at)
        known_speakers = {speaker.speaker_id for speaker in transcript.speakers}
        corrections = tuple(
            correction
            for correction in self.speaker_names
            if correction.speaker_id in known_speakers
        )
        if not corrections:
            return migrated
        return replace(migrated, revision=1, speaker_names=corrections)

    def _validate_transcript(self, transcript: TranscriptDocument) -> None:
        if transcript.session_id != self.session_id or transcript.run_id != self.run_id:
            raise ValueError("Review and transcript run IDs do not match")

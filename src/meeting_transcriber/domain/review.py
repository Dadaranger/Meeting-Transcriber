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


@dataclass(frozen=True, slots=True, order=True)
class SegmentSpeakerCorrection:
    segment_id: str
    speaker_id: str

    def __post_init__(self) -> None:
        UUID(self.segment_id)
        if not self.speaker_id.strip():
            raise ValueError("Corrected segment speaker ID cannot be blank")


@dataclass(frozen=True, slots=True)
class StructuredNotesCorrection:
    summary: str = ""
    decisions: tuple[str, ...] = ()
    action_items: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.summary != self.summary.strip():
            raise ValueError("Reviewed summary must be normalized")
        for field_name, items in (
            ("decision", self.decisions),
            ("action item", self.action_items),
        ):
            if any(not item or item != item.strip() for item in items):
                raise ValueError(f"Each reviewed {field_name} must be non-blank and normalized")


@dataclass(frozen=True, slots=True)
class TranscriptReview:
    """Sparse, versioned user corrections for exactly one transcript run."""

    SCHEMA_VERSION: ClassVar[int] = 3

    session_id: str
    run_id: str
    revision: int
    updated_at: datetime
    speaker_names: tuple[SpeakerNameCorrection, ...] = ()
    segment_texts: tuple[SegmentTextCorrection, ...] = ()
    segment_speakers: tuple[SegmentSpeakerCorrection, ...] = ()
    structured_notes: StructuredNotesCorrection | None = None

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
        if self.segment_speakers != tuple(sorted(self.segment_speakers)):
            raise ValueError("Segment speaker corrections must use deterministic ordering")
        speaker_ids = [correction.speaker_id for correction in self.speaker_names]
        segment_ids = [correction.segment_id for correction in self.segment_texts]
        assigned_segment_ids = [correction.segment_id for correction in self.segment_speakers]
        if len(speaker_ids) != len(set(speaker_ids)):
            raise ValueError("A review cannot correct one speaker more than once")
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("A review cannot correct one segment more than once")
        if len(assigned_segment_ids) != len(set(assigned_segment_ids)):
            raise ValueError("A review cannot reassign one segment more than once")

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
        assignments = {
            correction.segment_id: correction.speaker_id for correction in self.segment_speakers
        }
        speakers = {speaker.speaker_id: speaker for speaker in transcript.speakers}
        segments = {segment.segment_id: segment for segment in transcript.segments}
        known_speakers = speakers.keys()
        known_segments = segments.keys()
        unknown_speakers = names.keys() - known_speakers
        unknown_segments = (texts.keys() | assignments.keys()) - known_segments
        unknown_assigned_speakers = set(assignments.values()) - known_speakers
        if unknown_speakers:
            raise ValueError(f"Review references unknown speaker {min(unknown_speakers)!r}")
        if unknown_segments:
            raise ValueError(f"Review references unknown segment {min(unknown_segments)!r}")
        if unknown_assigned_speakers:
            raise ValueError(
                f"Review references unknown speaker {min(unknown_assigned_speakers)!r}"
            )
        for segment_id, speaker_id in assignments.items():
            segment = segments[segment_id]
            if speakers[segment.speaker_id].source != speakers[speaker_id].source:
                raise ValueError(
                    "A segment can only be assigned to a speaker from its audio source"
                )
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
                    speaker_id=assignments.get(segment.segment_id, segment.speaker_id),
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

    def assign_segment(
        self,
        transcript: TranscriptDocument,
        segment_id: str,
        speaker_id: str,
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
        source_speaker = next(
            speaker for speaker in transcript.speakers if speaker.speaker_id == segment.speaker_id
        )
        target_speaker = next(
            (speaker for speaker in transcript.speakers if speaker.speaker_id == speaker_id),
            None,
        )
        if target_speaker is None:
            raise ValueError(f"Unknown transcript speaker {speaker_id!r}")
        if target_speaker.source != source_speaker.source:
            raise ValueError("A segment can only be assigned to a speaker from its audio source")
        assignments = {
            correction.segment_id: correction.speaker_id for correction in self.segment_speakers
        }
        if speaker_id == segment.speaker_id:
            assignments.pop(segment_id, None)
        else:
            assignments[segment_id] = speaker_id
        updated = tuple(
            sorted(
                SegmentSpeakerCorrection(correction_id, assigned_speaker_id)
                for correction_id, assigned_speaker_id in assignments.items()
            )
        )
        if updated == self.segment_speakers:
            return self
        return replace(
            self,
            revision=self.revision + 1,
            updated_at=_timestamp(at),
            segment_speakers=updated,
        )

    def update_structured_notes(
        self,
        transcript: TranscriptDocument,
        summary: str,
        decisions: tuple[str, ...],
        action_items: tuple[str, ...],
        *,
        at: datetime | None = None,
    ) -> TranscriptReview:
        self._validate_transcript(transcript)
        updated = StructuredNotesCorrection(
            summary.strip(),
            tuple(item.strip() for item in decisions if item.strip()),
            tuple(item.strip() for item in action_items if item.strip()),
        )
        if updated == self.structured_notes:
            return self
        return replace(
            self,
            revision=self.revision + 1,
            updated_at=_timestamp(at),
            structured_notes=updated,
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
        if not corrections and self.structured_notes is None:
            return migrated
        return replace(
            migrated,
            revision=1,
            speaker_names=corrections,
            structured_notes=self.structured_notes,
        )

    def _validate_transcript(self, transcript: TranscriptDocument) -> None:
        if transcript.session_id != self.session_id or transcript.run_id != self.run_id:
            raise ValueError("Review and transcript run IDs do not match")

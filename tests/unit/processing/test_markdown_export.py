from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meeting_transcriber.domain.review import StructuredNotesCorrection
from meeting_transcriber.domain.session import MeetingSession, SessionState
from meeting_transcriber.domain.transcript import (
    TranscriptDocument,
    TranscriptionProfile,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
)
from meeting_transcriber.processing.text_export import render_meeting_notes

SESSION_ID = "0781afac-122c-465c-9852-ad8e7c6809d8"
RUN_ID = "4f51400b-d9a3-4681-b851-ece4d6b4d955"
START = datetime(2026, 8, 12, 2, 0, tzinfo=UTC)


def _session(title: str = "Project [Atlas] sync") -> MeetingSession:
    session = MeetingSession.new(title, session_id=SESSION_ID, now=START)
    session = session.confirm_consent(at=START + timedelta(seconds=1))
    session = session.transition(SessionState.RECORDING, at=START + timedelta(seconds=2))
    return session.transition(SessionState.RECORDED, at=START + timedelta(minutes=2))


def _transcript(*, segments: tuple[TranscriptSegment, ...] | None = None) -> TranscriptDocument:
    if segments is None:
        segments = (
            TranscriptSegment(
                segment_id="b945e43a-97a1-4161-af80-09833b75c11e",
                start_ms=1_250,
                end_ms=3_500,
                speaker_id="local",
                text="Review *Project Atlas* and [budget].",
                source=TranscriptSource.MICROPHONE,
                confidence=0.91,
            ),
            TranscriptSegment(
                segment_id="7b9dd095-3477-42a8-8a16-191807e38fef",
                start_ms=3_600,
                end_ms=65_010,
                speaker_id="remote",
                text="Approved.\nShip it.",
                source=TranscriptSource.SYSTEM_AUDIO,
            ),
        )
    return TranscriptDocument.new(
        SESSION_ID,
        run_id=RUN_ID,
        language="en",
        engine="faster-whisper",
        model="medium",
        profile=TranscriptionProfile.BALANCED,
        created_at=START + timedelta(minutes=3),
        speakers=(
            TranscriptSpeaker("local", "You", TranscriptSource.MICROPHONE),
            TranscriptSpeaker("remote", "Remote speakers", TranscriptSource.SYSTEM_AUDIO),
        ),
        segments=segments,
    )


def test_text_notes_are_structured_timestamped_and_plain() -> None:
    notes = render_meeting_notes(_session(), _transcript())

    assert notes.startswith("Project [Atlas] sync\n")
    assert "MEETING DETAILS\n" in notes
    assert "Duration: 00:01:05" in notes
    assert "- Remote speakers — System audio" in notes
    assert "00:00:01.250 to 00:00:03.500 | You | Microphone | 91% confidence" in notes
    assert "Review *Project Atlas* and [budget]." in notes
    assert "Approved. Ship it." in notes
    assert f"Transcript run: {RUN_ID}" in notes
    assert notes.endswith("\n")


def test_text_notes_make_an_empty_transcript_explicit() -> None:
    notes = render_meeting_notes(_session(), _transcript(segments=()))

    assert "Duration: 00:00:00" in notes
    assert "No reliable speech was detected." in notes


def test_text_notes_render_reviewed_structured_sections() -> None:
    notes = render_meeting_notes(
        _session(),
        _transcript(),
        StructuredNotesCorrection(
            "Aligned on the Project [Atlas] launch.",
            ("Ship on Friday", "Keep the local-first workflow"),
            ("Morgan: publish the revised plan",),
        ),
    )

    assert "SUMMARY\n-------\nAligned on the Project [Atlas] launch." in notes
    assert "DECISIONS\n---------\n- Ship on Friday" in notes
    assert "- Keep the local-first workflow" in notes
    assert "ACTION ITEMS\n------------\n[ ] Morgan: publish the revised plan" in notes


def test_text_notes_require_matching_session() -> None:
    other_session = MeetingSession.new("Other", now=START)

    with pytest.raises(ValueError, match="IDs do not match"):
        render_meeting_notes(other_session, _transcript())

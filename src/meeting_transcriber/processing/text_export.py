from __future__ import annotations

import re
from datetime import UTC, datetime

from meeting_transcriber.domain.review import StructuredNotesCorrection
from meeting_transcriber.domain.session import MeetingSession, SessionOrigin
from meeting_transcriber.domain.transcript import TranscriptDocument, TranscriptSource

SOURCE_LABELS = {
    TranscriptSource.MICROPHONE: "Microphone",
    TranscriptSource.SYSTEM_AUDIO: "System audio",
    TranscriptSource.IMPORTED_MEDIA: "Imported media",
}


def render_meeting_notes(
    session: MeetingSession,
    transcript: TranscriptDocument,
    structured_notes: StructuredNotesCorrection | None = None,
) -> str:
    """Render a deterministic plain-text record for one transcript."""

    if session.session_id != transcript.session_id:
        raise ValueError("Meeting session and transcript IDs do not match")

    notes = structured_notes or StructuredNotesCorrection()
    imported = session.origin is SessionOrigin.IMPORTED_MEDIA
    lines = [
        _heading(_plain_text(session.title), "="),
        "",
        (
            "Generated locally from imported media. Review the transcript before sharing it."
            if imported
            else "Generated locally from recorded audio. Review the transcript before sharing it."
        ),
        "",
        _heading("MEETING DETAILS"),
        (
            f"Imported: {_format_datetime(session.created_at)}"
            if imported
            else f"Recorded: {_format_datetime(session.started_at or session.created_at)}"
        ),
        f"Duration: {_format_duration(transcript.duration_ms)}",
        f"Language: {_plain_text(transcript.language)}",
        f"Transcription profile: {_plain_text(transcript.profile.value.title())}",
        f"Model: {_plain_text(transcript.model)}",
        "",
        _heading("SUMMARY"),
        _plain_text(notes.summary) if notes.summary else "No reviewed summary yet.",
        "",
        _heading("DECISIONS"),
    ]
    lines.extend(
        (f"- {_plain_text(decision)}" for decision in notes.decisions)
        if notes.decisions
        else ("No reviewed decisions recorded.",)
    )
    lines.extend(("", _heading("ACTION ITEMS")))
    lines.extend(
        (f"[ ] {_plain_text(item)}" for item in notes.action_items)
        if notes.action_items
        else ("No reviewed action items recorded.",)
    )
    lines.extend(("", _heading("PARTICIPANTS AND AUDIO SOURCES")))
    for speaker in transcript.speakers:
        lines.append(f"- {_plain_text(speaker.display_name)} — {SOURCE_LABELS[speaker.source]}")

    lines.extend(("", _heading("TRANSCRIPT")))
    speakers = {speaker.speaker_id: speaker for speaker in transcript.speakers}
    if not transcript.segments:
        lines.append(
            "No reliable speech was detected. Confirm the imported file contains audible speech."
            if imported
            else "No reliable speech was detected. Check the selected audio devices and levels."
        )
    for segment in transcript.segments:
        speaker = speakers[segment.speaker_id]
        timing = f"{_format_timestamp(segment.start_ms)} to {_format_timestamp(segment.end_ms)}"
        source = SOURCE_LABELS[segment.source]
        confidence = (
            f" | {segment.confidence * 100:.0f}% confidence"
            if segment.confidence is not None
            else ""
        )
        lines.extend(
            (
                f"{timing} | {_plain_text(speaker.display_name)} | {source}{confidence}",
                _plain_text(segment.text),
                "",
            )
        )

    lines.extend(
        (
            _heading("TECHNICAL DETAILS"),
            f"Meeting ID: {session.session_id}",
            f"Transcript run: {transcript.run_id}",
            f"Engine: {_plain_text(transcript.engine)}",
            f"Generated: {_format_datetime(transcript.created_at)}",
            "",
        )
    )
    return "\n".join(lines)


def _heading(text: str, underline: str = "-") -> str:
    return f"{text}\n{underline * max(3, len(text))}"


def _plain_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _format_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _format_duration(milliseconds: int) -> str:
    rounded_seconds = round(milliseconds / 1_000)
    hours, remainder = divmod(rounded_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")

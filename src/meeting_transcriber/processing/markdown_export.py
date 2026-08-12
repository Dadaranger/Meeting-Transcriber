from __future__ import annotations

import re
from datetime import UTC, datetime

from meeting_transcriber.domain.session import MeetingSession
from meeting_transcriber.domain.transcript import TranscriptDocument, TranscriptSource

SOURCE_LABELS = {
    TranscriptSource.MICROPHONE: "Microphone",
    TranscriptSource.SYSTEM_AUDIO: "System audio",
}


def render_meeting_notes(
    session: MeetingSession,
    transcript: TranscriptDocument,
) -> str:
    """Render a deterministic, editable Markdown record for one transcript."""

    if session.session_id != transcript.session_id:
        raise ValueError("Meeting session and transcript IDs do not match")

    lines = [
        f"# {_escape_markdown(session.title)}",
        "",
        "> Generated locally from recorded audio. Review the transcript before sharing it.",
        "",
        "## Meeting details",
        "",
        f"- **Recorded:** {_format_datetime(session.started_at or session.created_at)}",
        f"- **Duration:** {_format_duration(transcript.duration_ms)}",
        f"- **Language:** {_escape_markdown(transcript.language)}",
        f"- **Transcription profile:** {_escape_markdown(transcript.profile.value.title())}",
        f"- **Model:** {_escape_markdown(transcript.model)}",
        "",
        "## Participants and audio sources",
        "",
    ]
    for speaker in transcript.speakers:
        lines.append(
            f"- **{_escape_markdown(speaker.display_name)}** — {SOURCE_LABELS[speaker.source]}"
        )

    lines.extend(("", "## Transcript", ""))
    speakers = {speaker.speaker_id: speaker for speaker in transcript.speakers}
    if not transcript.segments:
        lines.append("_No speech was detected._")
    for segment in transcript.segments:
        speaker = speakers[segment.speaker_id]
        timing = f"{_format_timestamp(segment.start_ms)} to {_format_timestamp(segment.end_ms)}"
        source = SOURCE_LABELS[segment.source]
        confidence = (
            f" · {segment.confidence * 100:.0f}% confidence"
            if segment.confidence is not None
            else ""
        )
        lines.extend(
            (
                f"**{timing} · {_escape_markdown(speaker.display_name)} · {source}{confidence}**",
                "",
                _escape_markdown(segment.text),
                "",
            )
        )

    lines.extend(
        (
            "---",
            "",
            "## Technical details",
            "",
            f"- **Meeting ID:** `{session.session_id}`",
            f"- **Transcript run:** `{transcript.run_id}`",
            f"- **Engine:** {_escape_markdown(transcript.engine)}",
            f"- **Generated:** {_format_datetime(transcript.created_at)}",
            "",
        )
    )
    return "\n".join(lines)


def _escape_markdown(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    normalized = normalized.replace("\\", "\\\\")
    return re.sub(r"([`*_[\]<>#|])", r"\\\1", normalized)


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

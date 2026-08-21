from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid5

from meeting_transcriber.domain.diarization import DiarizationDocument, DiarizationTurn
from meeting_transcriber.domain.transcript import (
    TranscriptDocument,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
    TranscriptWord,
)


def merge_remote_speakers(
    transcript: TranscriptDocument,
    diarization: DiarizationDocument,
) -> TranscriptDocument:
    """Assign system-audio transcript words to stable anonymous remote speakers."""

    if transcript.session_id != diarization.session_id or transcript.run_id != diarization.run_id:
        raise ValueError("Transcript and diarization run IDs do not match")
    if not diarization.turns:
        return transcript

    segments: list[TranscriptSegment] = []
    for segment in transcript.segments:
        if segment.source is TranscriptSource.MICROPHONE:
            segments.append(segment)
            continue
        segments.extend(_assign_remote_segment(segment, diarization.turns))

    used_speaker_ids = {segment.speaker_id for segment in segments}
    original_speakers = {
        speaker.speaker_id: speaker
        for speaker in transcript.speakers
        if speaker.speaker_id in used_speaker_ids
    }
    remote_speaker_ids = []
    for turn in diarization.turns:
        if turn.speaker_id not in remote_speaker_ids and turn.speaker_id in used_speaker_ids:
            remote_speaker_ids.append(turn.speaker_id)
    speakers = list(original_speakers.values())
    remote_source = next(
        (
            segment.source
            for segment in segments
            if segment.source is not TranscriptSource.MICROPHONE
        ),
        TranscriptSource.SYSTEM_AUDIO,
    )
    speakers.extend(
        TranscriptSpeaker(
            speaker_id,
            f"Remote Speaker {index}",
            remote_source,
        )
        for index, speaker_id in enumerate(remote_speaker_ids, start=1)
        if speaker_id not in original_speakers
    )
    return replace(
        transcript,
        speakers=tuple(speakers),
        segments=tuple(
            sorted(
                segments,
                key=lambda item: (item.start_ms, item.end_ms, item.segment_id),
            )
        ),
    )


def _assign_remote_segment(
    segment: TranscriptSegment,
    turns: tuple[DiarizationTurn, ...],
) -> list[TranscriptSegment]:
    if not segment.words:
        speaker_id = _best_speaker(segment.start_ms, segment.end_ms, turns)
        return [replace(segment, speaker_id=speaker_id or segment.speaker_id)]

    assignments = [
        (_best_speaker(word.start_ms, word.end_ms, turns) or segment.speaker_id, word)
        for word in segment.words
    ]
    groups: list[tuple[str, list[TranscriptWord]]] = []
    for speaker_id, word in assignments:
        if groups and groups[-1][0] == speaker_id:
            groups[-1][1].append(word)
        else:
            groups.append((speaker_id, [word]))
    if len(groups) == 1:
        return [replace(segment, speaker_id=groups[0][0])]

    split: list[TranscriptSegment] = []
    for index, (speaker_id, words) in enumerate(groups):
        split.append(
            replace(
                segment,
                segment_id=str(
                    uuid5(UUID(segment.segment_id), f"diarization:{index}:{speaker_id}")
                ),
                start_ms=words[0].start_ms,
                end_ms=words[-1].end_ms,
                speaker_id=speaker_id,
                text=_join_words(words),
                words=tuple(words),
            )
        )
    return split


def _best_speaker(
    start_ms: int,
    end_ms: int,
    turns: tuple[DiarizationTurn, ...],
) -> str | None:
    best_speaker: str | None = None
    best_overlap = 0
    for turn in turns:
        if turn.start_ms >= end_ms:
            break
        overlap = min(end_ms, turn.end_ms) - max(start_ms, turn.start_ms)
        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = turn.speaker_id
    return best_speaker


def _join_words(words: list[TranscriptWord]) -> str:
    text = ""
    for word in words:
        token = word.text
        if text and not token[:1].isspace() and text[-1:].isalnum() and token[:1].isalnum():
            text += " "
        text += token
    return text.strip()

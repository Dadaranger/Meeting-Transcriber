from __future__ import annotations

from datetime import UTC, datetime

from meeting_transcriber.domain.diarization import DiarizationDocument, DiarizationTurn
from meeting_transcriber.domain.transcript import (
    TranscriptDocument,
    TranscriptionProfile,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
    TranscriptWord,
)
from meeting_transcriber.processing.diarization_merge import merge_remote_speakers

SESSION_ID = "1d7ee70c-7c04-4d17-903b-7b55152ea495"
RUN_ID = "66c62603-793c-4940-b76b-4b5355347144"
START = datetime(2026, 8, 12, 6, 0, tzinfo=UTC)


def _transcript() -> TranscriptDocument:
    return TranscriptDocument.new(
        SESSION_ID,
        run_id=RUN_ID,
        language="en",
        engine="test",
        model="medium",
        profile=TranscriptionProfile.BALANCED,
        created_at=START,
        speakers=(
            TranscriptSpeaker("local", "You", TranscriptSource.MICROPHONE),
            TranscriptSpeaker("remote", "Remote speakers", TranscriptSource.SYSTEM_AUDIO),
        ),
        segments=(
            TranscriptSegment(
                "1cdce379-f4ca-4327-8a3d-f8fc4f722e0d",
                0,
                900,
                "local",
                "I can hear both of you.",
                TranscriptSource.MICROPHONE,
            ),
            TranscriptSegment(
                "3d3820e7-f96d-41c8-ab50-ebbe32fe2461",
                100,
                1_900,
                "remote",
                "Hello there General Kenobi",
                TranscriptSource.SYSTEM_AUDIO,
                words=(
                    TranscriptWord("Hello", 100, 400),
                    TranscriptWord("there", 450, 800),
                    TranscriptWord("General", 1_100, 1_400),
                    TranscriptWord("Kenobi", 1_450, 1_800),
                ),
            ),
            TranscriptSegment(
                "19c64dce-b456-4983-97d0-33618c84e962",
                2_100,
                2_500,
                "remote",
                "Uncovered speech",
                TranscriptSource.SYSTEM_AUDIO,
            ),
        ),
    )


def _diarization() -> DiarizationDocument:
    return DiarizationDocument.new(
        SESSION_ID,
        RUN_ID,
        engine="fixture",
        model="community-1",
        created_at=START,
        turns=(
            DiarizationTurn(0, 1_000, "remote-1"),
            DiarizationTurn(1_000, 2_000, "remote-2"),
        ),
    )


def test_merge_splits_remote_words_and_preserves_overlapping_microphone() -> None:
    merged = merge_remote_speakers(_transcript(), _diarization())

    assert [(speaker.speaker_id, speaker.display_name) for speaker in merged.speakers] == [
        ("local", "You"),
        ("remote", "Remote speakers"),
        ("remote-1", "Remote Speaker 1"),
        ("remote-2", "Remote Speaker 2"),
    ]
    assert [
        (segment.start_ms, segment.end_ms, segment.speaker_id, segment.text)
        for segment in merged.segments
    ] == [
        (0, 900, "local", "I can hear both of you."),
        (100, 800, "remote-1", "Hello there"),
        (1_100, 1_800, "remote-2", "General Kenobi"),
        (2_100, 2_500, "remote", "Uncovered speech"),
    ]
    assert len(merged.segments[1].words) == 2
    assert merged.segments[0].segment_id == _transcript().segments[0].segment_id


def test_merge_assigns_segment_without_words_by_largest_overlap() -> None:
    transcript = _transcript()
    no_words = transcript.segments[2]
    changed = TranscriptSegment(
        no_words.segment_id,
        900,
        1_600,
        no_words.speaker_id,
        no_words.text,
        no_words.source,
    )
    transcript = TranscriptDocument.new(
        SESSION_ID,
        run_id=RUN_ID,
        language="en",
        engine="test",
        model="medium",
        profile=TranscriptionProfile.BALANCED,
        created_at=START,
        speakers=transcript.speakers,
        segments=(changed,),
    )

    merged = merge_remote_speakers(transcript, _diarization())

    assert merged.segments[0].speaker_id == "remote-2"


def test_empty_diarization_leaves_transcript_unchanged() -> None:
    empty = DiarizationDocument.new(
        SESSION_ID,
        RUN_ID,
        engine="fixture",
        model="community-1",
        turns=(),
    )

    assert merge_remote_speakers(_transcript(), empty) == _transcript()

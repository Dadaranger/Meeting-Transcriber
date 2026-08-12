from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meeting_transcriber.domain.transcript import (
    InvalidTranscriptionJobTransition,
    TranscriptDocument,
    TranscriptionJob,
    TranscriptionJobState,
    TranscriptionProfile,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
    TranscriptWord,
)

SESSION_ID = "f65e43d7-f6bd-4dd0-9e6c-95af667fc2a3"
RUN_ID = "58adf7d2-8e03-4fb4-b9ce-5f82e2636712"
SEGMENT_ID = "49f1ec2d-735e-41f3-88b4-040331253bf7"
JOB_ID = "a2aa6282-7eb7-49e7-b31e-7af7b2de8c1e"
START = datetime(2026, 8, 12, 1, 0, tzinfo=UTC)


def _transcript() -> TranscriptDocument:
    return TranscriptDocument.new(
        SESSION_ID,
        run_id=RUN_ID,
        language="en",
        engine="faster-whisper",
        model="medium.en",
        profile=TranscriptionProfile.BALANCED,
        created_at=START,
        speakers=(
            TranscriptSpeaker("local", "You", TranscriptSource.MICROPHONE),
            TranscriptSpeaker("remote", "Remote speakers", TranscriptSource.SYSTEM_AUDIO),
        ),
        segments=(
            TranscriptSegment(
                segment_id=SEGMENT_ID,
                start_ms=1_000,
                end_ms=2_000,
                speaker_id="local",
                text="Hello there.",
                source=TranscriptSource.MICROPHONE,
                confidence=0.93,
                words=(
                    TranscriptWord("Hello", 1_000, 1_400, 0.95),
                    TranscriptWord("there.", 1_450, 2_000, 0.91),
                ),
            ),
        ),
    )


def test_transcript_requires_source_consistent_speakers_and_timing() -> None:
    transcript = _transcript()

    assert transcript.duration_ms == 2_000
    assert transcript.segments[0].words[0].probability == 0.95

    with pytest.raises(ValueError, match="source must match"):
        TranscriptDocument.new(
            SESSION_ID,
            language="en",
            engine="test",
            model="test",
            profile=TranscriptionProfile.FAST,
            speakers=(TranscriptSpeaker("local", "You", TranscriptSource.MICROPHONE),),
            segments=(
                TranscriptSegment(
                    segment_id=SEGMENT_ID,
                    start_ms=0,
                    end_ms=1_000,
                    speaker_id="local",
                    text="Wrong source",
                    source=TranscriptSource.SYSTEM_AUDIO,
                ),
            ),
        )


def test_transcript_segments_must_be_deterministically_ordered() -> None:
    first = _transcript().segments[0]
    earlier = TranscriptSegment(
        segment_id="e410170f-326b-4165-83a3-73a1acdb950a",
        start_ms=0,
        end_ms=500,
        speaker_id="local",
        text="Earlier",
        source=TranscriptSource.MICROPHONE,
    )

    with pytest.raises(ValueError, match="chronological"):
        TranscriptDocument.new(
            SESSION_ID,
            language="en",
            engine="test",
            model="test",
            profile=TranscriptionProfile.FAST,
            speakers=(TranscriptSpeaker("local", "You", TranscriptSource.MICROPHONE),),
            segments=(first, earlier),
        )


def test_transcription_job_tracks_progress_failure_and_retry() -> None:
    job = TranscriptionJob.new(
        SESSION_ID,
        job_id=JOB_ID,
        profile=TranscriptionProfile.ACCURATE,
        language="en",
        created_at=START,
    )
    job = job.transition(TranscriptionJobState.PREPARING, at=START + timedelta(seconds=1))
    job = job.with_progress(100, 1_000, at=START + timedelta(seconds=2))
    job = job.transition(TranscriptionJobState.TRANSCRIBING, at=START + timedelta(seconds=3))
    job = job.with_progress(400, 1_000, at=START + timedelta(seconds=4))
    job = job.transition(
        TranscriptionJobState.FAILED,
        at=START + timedelta(seconds=5),
        error="Synthetic engine failure",
    )

    assert job.progress == 0.4
    assert job.error == "Synthetic engine failure"

    retried = job.retry(at=START + timedelta(seconds=6))

    assert retried.state is TranscriptionJobState.PENDING
    assert retried.attempt == 2
    assert retried.progress == 0.0
    assert retried.error is None


def test_completed_job_cannot_transition_again() -> None:
    job = TranscriptionJob.new(SESSION_ID, job_id=JOB_ID, created_at=START)
    job = job.transition(TranscriptionJobState.PREPARING)
    job = job.with_progress(0, 1_000)
    job = job.transition(TranscriptionJobState.TRANSCRIBING)
    job = job.transition(TranscriptionJobState.COMPLETED)

    assert job.progress == 1.0
    assert job.processed_audio_ms == 1_000
    with pytest.raises(InvalidTranscriptionJobTransition):
        job.transition(TranscriptionJobState.CANCELLED)


def test_remote_speaker_job_tracks_diarization_and_nonfatal_warning() -> None:
    job = TranscriptionJob.new(
        SESSION_ID,
        job_id=JOB_ID,
        created_at=START,
        separate_remote_speakers=True,
        min_remote_speakers=2,
        max_remote_speakers=4,
    )
    job = job.transition(TranscriptionJobState.PREPARING)
    job = job.with_progress(1_000, 1_000)
    job = job.transition(TranscriptionJobState.TRANSCRIBING)
    job = job.transition(TranscriptionJobState.DIARIZING)
    job = job.with_warning("Optional model unavailable")
    completed = job.transition(TranscriptionJobState.COMPLETED)

    assert completed.progress == 1.0
    assert completed.warning == "Optional model unavailable"
    assert completed.min_remote_speakers == 2
    assert completed.max_remote_speakers == 4

    with pytest.raises(ValueError, match="cannot exceed"):
        TranscriptionJob.new(
            SESSION_ID,
            separate_remote_speakers=True,
            min_remote_speakers=3,
            max_remote_speakers=2,
        )

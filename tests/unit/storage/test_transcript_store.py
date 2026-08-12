from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from meeting_transcriber.domain.transcript import (
    TranscriptDocument,
    TranscriptionJob,
    TranscriptionProfile,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
    TranscriptWord,
)
from meeting_transcriber.storage.transcript_store import (
    TranscriptStore,
    UnsupportedTranscriptSchema,
)

SESSION_ID = "e21813c4-9846-49b5-a4e3-55ffb18b0c01"
RUN_ID = "3bf4c384-1509-46c7-abf2-b94c2a91e914"
START = datetime(2026, 8, 12, 2, 0, tzinfo=UTC)


def _transcript(run_id: str = RUN_ID, text: str = "Hello") -> TranscriptDocument:
    return TranscriptDocument.new(
        SESSION_ID,
        run_id=run_id,
        language="en",
        engine="fixture",
        model="fixture-1",
        profile=TranscriptionProfile.FAST,
        created_at=START,
        speakers=(TranscriptSpeaker("local", "You", TranscriptSource.MICROPHONE),),
        segments=(
            TranscriptSegment(
                segment_id="d27059b3-c15d-455d-8b5e-a4b06567909a",
                start_ms=0,
                end_ms=500,
                speaker_id="local",
                text=text,
                source=TranscriptSource.MICROPHONE,
                confidence=0.9,
                words=(TranscriptWord(text, 0, 500, 0.9),),
            ),
        ),
    )


def test_transcript_round_trip_retains_run_and_updates_canonical(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    first = _transcript()
    second = _transcript("70ea3500-44e4-41a6-a03e-91ca36ffca47", "Updated")

    canonical_path = store.save_transcript(first)
    store.save_transcript(second)

    assert store.load_transcript(SESSION_ID) == second
    assert store.load_transcript(SESSION_ID, RUN_ID) == first
    assert canonical_path.name == "transcript.json"
    document = json.loads(canonical_path.read_text(encoding="utf-8"))
    assert document["schema_version"] == TranscriptDocument.SCHEMA_VERSION
    assert document["segments"][0]["words"][0]["probability"] == 0.9
    assert list(canonical_path.parent.glob("*.tmp")) == []


def test_transcription_job_round_trip_is_separate_from_transcript(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    job = TranscriptionJob.new(
        SESSION_ID,
        job_id="175ec0b8-bb8c-49c3-9d02-e4b5d6ae3807",
        profile=TranscriptionProfile.BALANCED,
        language="en",
        separate_remote_speakers=True,
        min_remote_speakers=2,
        max_remote_speakers=4,
        created_at=START,
    )

    path = store.save_job(job)

    assert store.load_job(SESSION_ID) == job
    assert path.name == "transcription-job.json"
    assert path.parent.name == "processing"


def test_version_one_transcription_job_loads_with_diarization_disabled(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    job = TranscriptionJob.new(
        SESSION_ID,
        job_id="175ec0b8-bb8c-49c3-9d02-e4b5d6ae3807",
        created_at=START,
    )
    path = store.save_job(job)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = 1
    for field in (
        "separate_remote_speakers",
        "min_remote_speakers",
        "max_remote_speakers",
        "warning",
    ):
        document.pop(field)
    path.write_text(json.dumps(document), encoding="utf-8")

    loaded = store.load_job(SESSION_ID)

    assert not loaded.separate_remote_speakers
    assert loaded.min_remote_speakers is None
    assert loaded.max_remote_speakers is None
    assert loaded.warning is None


def test_unknown_transcript_schema_is_rejected(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path)
    path = store.save_transcript(_transcript())
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = 999
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(UnsupportedTranscriptSchema, match="999"):
        store.load_transcript(SESSION_ID)

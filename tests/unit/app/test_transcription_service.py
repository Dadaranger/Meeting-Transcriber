from __future__ import annotations

from pathlib import Path
from threading import Event

from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.app.transcription_service import MeetingTranscriptionService
from meeting_transcriber.domain.session import SessionState
from meeting_transcriber.domain.transcript import (
    TranscriptionJob,
    TranscriptionJobState,
    TranscriptionProfile,
    TranscriptSource,
)
from meeting_transcriber.processing.engine import (
    ChunkTranscription,
    EngineSegment,
    EngineWord,
    TranscriptionCancelled,
    TranscriptionEngine,
)
from meeting_transcriber.processing.preparation import PreparedAudioChunk, PreparedAudioPlan
from meeting_transcriber.storage.meeting_notes_store import MeetingNotesStore
from meeting_transcriber.storage.session_store import SessionStore
from meeting_transcriber.storage.transcript_store import TranscriptStore


class FakePreparer:
    def __init__(self, session_id: str, tmp_path: Path):
        self.session_id = session_id
        self.tmp_path = tmp_path
        self.run_ids: list[str] = []

    def prepare(self, session_directory: Path, run_id: str) -> PreparedAudioPlan:
        assert session_directory == self.tmp_path / self.session_id
        self.run_ids.append(run_id)
        chunks = (
            PreparedAudioChunk(
                TranscriptSource.MICROPHONE,
                1,
                session_directory / "derived" / "microphone.wav",
                0,
                1_000,
                16_000,
            ),
            PreparedAudioChunk(
                TranscriptSource.SYSTEM_AUDIO,
                1,
                session_directory / "derived" / "system.wav",
                500,
                1_000,
                16_000,
            ),
        )
        return PreparedAudioPlan(self.session_id, run_id, chunks, 2_000, 1_500)


class FakeEngine:
    def __init__(self, *, fail: bool = False, block_until_cancelled: bool = False):
        self.fail = fail
        self.block_until_cancelled = block_until_cancelled
        self.started = Event()
        self.calls: list[PreparedAudioChunk] = []

    @property
    def engine_name(self) -> str:
        return "fixture-engine"

    @property
    def model_name(self) -> str:
        return "fixture-model"

    def transcribe_chunk(
        self,
        chunk: PreparedAudioChunk,
        *,
        language: str | None,
        hotwords: str | None,
        cancel_requested: object,
    ) -> ChunkTranscription:
        del language, hotwords
        self.calls.append(chunk)
        self.started.set()
        if not callable(cancel_requested):
            raise TypeError("Cancellation callback is required")
        if self.block_until_cancelled:
            while not cancel_requested():
                self.started.wait(0.01)
            raise TranscriptionCancelled("Synthetic cancellation")
        if self.fail:
            raise RuntimeError("Synthetic model failure")
        text = "Hello" if chunk.source is TranscriptSource.MICROPHONE else "Remote reply"
        return ChunkTranscription(
            language="en",
            language_probability=0.9,
            segments=(
                EngineSegment(
                    text,
                    100,
                    500,
                    0.85,
                    (EngineWord(text, 100, 500, 0.85),),
                ),
            ),
        )


class FakeEngineFactory:
    def __init__(self, engines: list[FakeEngine]):
        self.engines = engines
        self.calls: list[tuple[TranscriptionProfile, bool]] = []

    def __call__(
        self,
        profile: TranscriptionProfile,
        *,
        allow_download: bool,
    ) -> TranscriptionEngine:
        self.calls.append((profile, allow_download))
        return self.engines.pop(0)


def _recorded_session(service: MeetingSessionService) -> str:
    draft = service.create_draft("Recorded meeting")
    service.confirm_recording_consent(draft.session_id)
    service.transition_state(draft.session_id, SessionState.RECORDING)
    service.transition_state(draft.session_id, SessionState.RECORDED)
    return draft.session_id


def _service(
    tmp_path: Path,
    engines: list[FakeEngine],
) -> tuple[
    MeetingTranscriptionService,
    MeetingSessionService,
    TranscriptStore,
    FakePreparer,
    FakeEngineFactory,
    str,
]:
    sessions = MeetingSessionService(SessionStore(tmp_path))
    session_id = _recorded_session(sessions)
    transcripts = TranscriptStore(tmp_path)
    preparer = FakePreparer(session_id, tmp_path)
    factory = FakeEngineFactory(engines)
    service = MeetingTranscriptionService(
        sessions,
        transcripts,
        tmp_path / "models",
        preparer=preparer,
        engine_factory=factory,
    )
    return service, sessions, transcripts, preparer, factory, session_id


def test_transcription_merges_sources_and_persists_ready_transcript(tmp_path: Path) -> None:
    engine = FakeEngine()
    service, sessions, transcripts, preparer, factory, session_id = _service(tmp_path, [engine])

    started = service.start(
        session_id,
        profile=TranscriptionProfile.BALANCED,
        language="en",
        hotwords="WASAPI",
        allow_download=False,
    )
    completed = service.wait()

    assert completed.state is TranscriptionJobState.COMPLETED
    assert completed.progress == 1.0
    assert sessions.get_session(session_id).state is SessionState.READY
    transcript = transcripts.load_transcript(session_id)
    assert transcript.run_id == started.job_id
    assert transcript.engine == "fixture-engine"
    assert transcript.model == "fixture-model"
    assert [
        (segment.start_ms, segment.speaker_id, segment.text) for segment in transcript.segments
    ] == [
        (100, "local", "Hello"),
        (600, "remote", "Remote reply"),
    ]
    assert transcript.segments[1].words[0].start_ms == 600
    notes = MeetingNotesStore(tmp_path).notes_file(session_id).read_text(encoding="utf-8")
    assert notes.startswith("# Recorded meeting\n")
    assert "**00:00:00.100 to 00:00:00.500 · You · Microphone" in notes
    assert "Remote reply" in notes
    assert preparer.run_ids == [started.job_id]
    assert factory.calls == [(TranscriptionProfile.BALANCED, False)]


def test_transcription_cancel_returns_session_to_recorded(tmp_path: Path) -> None:
    engine = FakeEngine(block_until_cancelled=True)
    service, sessions, transcripts, _preparer, _factory, session_id = _service(tmp_path, [engine])
    service.start(
        session_id,
        profile=TranscriptionProfile.FAST,
        language=None,
        hotwords=None,
        allow_download=False,
    )
    assert engine.started.wait(2)

    service.cancel()
    cancelled = service.wait()

    assert cancelled.state is TranscriptionJobState.CANCELLED
    assert sessions.get_session(session_id).state is SessionState.RECORDED
    assert not transcripts.transcript_file(session_id).exists()


def test_failed_transcription_retry_reuses_run_and_prepared_audio(tmp_path: Path) -> None:
    service, sessions, transcripts, preparer, _factory, session_id = _service(
        tmp_path,
        [FakeEngine(fail=True), FakeEngine()],
    )
    first = service.start(
        session_id,
        profile=TranscriptionProfile.FAST,
        language="en",
        hotwords=None,
        allow_download=False,
    )
    failed = service.wait()

    assert failed.state is TranscriptionJobState.FAILED
    assert "Synthetic model failure" in (failed.error or "")
    assert sessions.get_session(session_id).state is SessionState.RECORDED

    retried = service.start(
        session_id,
        profile=TranscriptionProfile.FAST,
        language="en",
        hotwords=None,
        allow_download=False,
    )
    completed = service.wait()

    assert retried.job_id == first.job_id
    assert retried.attempt == 2
    assert completed.state is TranscriptionJobState.COMPLETED
    assert preparer.run_ids == [first.job_id, first.job_id]
    assert transcripts.load_transcript(session_id).run_id == first.job_id


class FailOnceNotesStore:
    def __init__(self, delegate: MeetingNotesStore):
        self.delegate = delegate
        self.calls = 0

    def save(self, session_id: str, run_id: str, markdown: str) -> Path:
        self.calls += 1
        if self.calls == 1:
            raise OSError("Synthetic notes write failure")
        return self.delegate.save(session_id, run_id, markdown)


def test_notes_failure_retry_reuses_completed_transcript_without_model_rerun(
    tmp_path: Path,
) -> None:
    sessions = MeetingSessionService(SessionStore(tmp_path))
    session_id = _recorded_session(sessions)
    transcripts = TranscriptStore(tmp_path)
    preparer = FakePreparer(session_id, tmp_path)
    factory = FakeEngineFactory([FakeEngine()])
    notes = FailOnceNotesStore(MeetingNotesStore(tmp_path))
    service = MeetingTranscriptionService(
        sessions,
        transcripts,
        tmp_path / "models",
        preparer=preparer,
        engine_factory=factory,
        notes_store=notes,
    )

    first = service.start(
        session_id,
        profile=TranscriptionProfile.BALANCED,
        language="en",
        hotwords=None,
        allow_download=False,
    )
    failed = service.wait()

    assert failed.state is TranscriptionJobState.FAILED
    assert "Synthetic notes write failure" in (failed.error or "")
    assert transcripts.load_transcript(session_id, first.job_id).run_id == first.job_id
    assert sessions.get_session(session_id).state is SessionState.RECORDED

    retried = service.start(
        session_id,
        profile=TranscriptionProfile.BALANCED,
        language="en",
        hotwords=None,
        allow_download=False,
    )
    completed = service.wait()

    assert retried.job_id == first.job_id
    assert completed.state is TranscriptionJobState.COMPLETED
    assert sessions.get_session(session_id).state is SessionState.READY
    assert notes.calls == 2
    assert preparer.run_ids == [first.job_id]
    assert factory.engines == []
    assert MeetingNotesStore(tmp_path).notes_file(session_id).exists()


def test_startup_recovers_interrupted_transcription_job(tmp_path: Path) -> None:
    service, sessions, transcripts, _preparer, _factory, session_id = _service(
        tmp_path, [FakeEngine()]
    )
    job = TranscriptionJob.new(session_id)
    job = job.transition(TranscriptionJobState.PREPARING)
    job = job.with_progress(100, 1_000)
    job = job.transition(TranscriptionJobState.TRANSCRIBING)
    transcripts.save_job(job)
    sessions.transition_state(session_id, SessionState.PROCESSING)

    recovered = service.recover_interrupted_jobs()

    assert len(recovered) == 1
    assert recovered[0].state is TranscriptionJobState.FAILED
    assert sessions.get_session(session_id).state is SessionState.RECORDED
    assert transcripts.load_job(session_id).error is not None

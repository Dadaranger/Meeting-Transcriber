from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event

from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.app.transcription_service import (
    MeetingTranscriptionService,
    RemoteSpeakerProcessor,
)
from meeting_transcriber.domain.diarization import DiarizationDocument, DiarizationTurn
from meeting_transcriber.domain.session import SessionState
from meeting_transcriber.domain.transcript import (
    TranscriptDocument,
    TranscriptionJob,
    TranscriptionJobState,
    TranscriptionProfile,
    TranscriptSource,
)
from meeting_transcriber.processing.diarization_engine import (
    DiarizationCancelled,
    DiarizationRuntimeError,
)
from meeting_transcriber.processing.diarization_merge import merge_remote_speakers
from meeting_transcriber.processing.engine import (
    ChunkTranscription,
    EngineSegment,
    EngineWord,
    TranscriptionCancelled,
    TranscriptionEngine,
)
from meeting_transcriber.processing.preparation import PreparedAudioChunk, PreparedAudioPlan
from meeting_transcriber.storage.meeting_notes_store import MeetingNotesStore
from meeting_transcriber.storage.review_store import ReviewStore
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
        self.prepared = False

    @property
    def engine_name(self) -> str:
        return "fixture-engine"

    @property
    def model_name(self) -> str:
        return "fixture-model"

    def prepare(
        self,
        *,
        cancel_requested: Callable[[], bool],
        progress_callback: Callable[[int, int], None],
    ) -> None:
        if cancel_requested():
            raise TranscriptionCancelled("Synthetic cancellation")
        progress_callback(50, 100)
        progress_callback(100, 100)
        self.prepared = True

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


class FakeRemoteSpeakerProcessor:
    def __init__(self, errors: list[Exception] | None = None):
        self.errors = errors or []
        self.calls: list[dict[str, object]] = []

    def separate(
        self,
        plan: PreparedAudioPlan,
        session_directory: Path,
        transcript: TranscriptDocument,
        *,
        allow_download: bool,
        access_token: str | None,
        min_speakers: int | None,
        max_speakers: int | None,
        cancel_requested: Callable[[], bool],
    ) -> TranscriptDocument:
        self.calls.append(
            {
                "plan": plan,
                "session_directory": session_directory,
                "allow_download": allow_download,
                "access_token": access_token,
                "min_speakers": min_speakers,
                "max_speakers": max_speakers,
                "cancel_requested": cancel_requested,
            }
        )
        if self.errors:
            raise self.errors.pop(0)
        return merge_remote_speakers(
            transcript,
            DiarizationDocument.new(
                transcript.session_id,
                transcript.run_id,
                engine="fixture-diarization",
                model="fixture-speaker-model",
                turns=(DiarizationTurn(0, 2_000, "remote-1"),),
            ),
        )


def _recorded_session(service: MeetingSessionService) -> str:
    draft = service.create_draft("Recorded meeting")
    service.confirm_recording_consent(draft.session_id)
    service.transition_state(draft.session_id, SessionState.RECORDING)
    service.transition_state(draft.session_id, SessionState.RECORDED)
    return draft.session_id


def _service(
    tmp_path: Path,
    engines: list[FakeEngine],
    *,
    remote_speaker_processor: RemoteSpeakerProcessor | None = None,
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
        remote_speaker_processor=remote_speaker_processor,
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
    assert notes.startswith("Recorded meeting\n================\n")
    assert "00:00:00.100 to 00:00:00.500 | You | Microphone" in notes
    assert "Remote reply" in notes
    assert preparer.run_ids == [started.job_id]
    assert factory.calls == [(TranscriptionProfile.BALANCED, False)]
    assert engine.prepared
    assert completed.model_downloaded_bytes == 100
    assert completed.model_total_bytes == 100


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


def test_remote_speaker_separation_is_persisted_without_access_token(tmp_path: Path) -> None:
    processor = FakeRemoteSpeakerProcessor()
    service, sessions, transcripts, _preparer, factory, session_id = _service(
        tmp_path,
        [FakeEngine()],
        remote_speaker_processor=processor,
    )

    service.start(
        session_id,
        profile=TranscriptionProfile.BALANCED,
        language="en",
        hotwords=None,
        allow_download=False,
        separate_remote_speakers=True,
        min_remote_speakers=1,
        max_remote_speakers=3,
        diarization_allow_download=True,
        diarization_access_token="temporary-token",
    )
    completed = service.wait()

    assert completed.state is TranscriptionJobState.COMPLETED
    assert completed.warning is None
    assert completed.separate_remote_speakers
    assert sessions.get_session(session_id).state is SessionState.READY
    assert transcripts.load_transcript(session_id).segments[1].speaker_id == "remote-1"
    call = processor.calls[0]
    assert call["min_speakers"] == 1
    assert call["max_speakers"] == 3
    assert call["access_token"] == "temporary-token"
    assert factory.calls == [(TranscriptionProfile.BALANCED, False)]
    persisted_job = transcripts.job_file(session_id).read_text(encoding="utf-8")
    assert "temporary-token" not in persisted_job


def test_known_diarization_failure_completes_with_raw_transcript_warning(tmp_path: Path) -> None:
    processor = FakeRemoteSpeakerProcessor(
        [DiarizationRuntimeError("The optional runtime is not installed")]
    )
    service, sessions, transcripts, _preparer, _factory, session_id = _service(
        tmp_path,
        [FakeEngine()],
        remote_speaker_processor=processor,
    )

    service.start(
        session_id,
        profile=TranscriptionProfile.FAST,
        language="en",
        hotwords=None,
        allow_download=False,
        separate_remote_speakers=True,
    )
    completed = service.wait()

    assert completed.state is TranscriptionJobState.COMPLETED
    assert "optional runtime" in (completed.warning or "")
    assert sessions.get_session(session_id).state is SessionState.READY
    assert transcripts.load_transcript(session_id).segments[1].speaker_id == "remote"


def test_cancelled_diarization_retry_reuses_raw_transcript_without_whisper(
    tmp_path: Path,
) -> None:
    processor = FakeRemoteSpeakerProcessor(
        [DiarizationCancelled("Synthetic diarization cancellation")]
    )
    service, _sessions, transcripts, preparer, factory, session_id = _service(
        tmp_path,
        [FakeEngine()],
        remote_speaker_processor=processor,
    )
    first = service.start(
        session_id,
        profile=TranscriptionProfile.BALANCED,
        language="en",
        hotwords=None,
        allow_download=False,
        separate_remote_speakers=True,
    )
    cancelled = service.wait()
    retried = service.start(
        session_id,
        profile=TranscriptionProfile.BALANCED,
        language="en",
        hotwords=None,
        allow_download=False,
        separate_remote_speakers=True,
    )
    completed = service.wait()

    assert cancelled.state is TranscriptionJobState.CANCELLED
    assert retried.job_id == first.job_id
    assert completed.state is TranscriptionJobState.COMPLETED
    assert transcripts.load_transcript(session_id).segments[1].speaker_id == "remote-1"
    assert len(processor.calls) == 2
    assert preparer.run_ids == [first.job_id, first.job_id]
    assert factory.calls == [(TranscriptionProfile.BALANCED, False)]


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

    def save(
        self,
        session_id: str,
        run_id: str,
        text: str,
        *,
        output_filename: str | None = None,
    ) -> Path:
        self.calls += 1
        if self.calls == 1:
            raise OSError("Synthetic notes write failure")
        return self.delegate.save(
            session_id,
            run_id,
            text,
            output_filename=output_filename,
        )


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


def test_new_transcription_run_migrates_names_without_stale_text_edits(tmp_path: Path) -> None:
    service, _sessions, transcripts, _preparer, factory, session_id = _service(
        tmp_path,
        [FakeEngine(), FakeEngine()],
    )
    service.start(
        session_id,
        profile=TranscriptionProfile.BALANCED,
        language="en",
        hotwords=None,
        allow_download=False,
    )
    service.wait()
    first_transcript = transcripts.load_transcript(session_id)
    remote_segment = first_transcript.segments[1]
    reviews = ReviewStore(tmp_path)
    review = reviews.load_for_transcript(first_transcript)
    review = review.rename_speaker(first_transcript, "remote", "Morgan")
    review = review.correct_segment(first_transcript, remote_segment.segment_id, "Old correction")
    reviews.save(review)

    second_job = service.start(
        session_id,
        profile=TranscriptionProfile.ACCURATE,
        language="en",
        hotwords=None,
        allow_download=False,
    )
    service.wait()

    second_transcript = transcripts.load_transcript(session_id)
    migrated = reviews.load(session_id)
    markdown = MeetingNotesStore(tmp_path).notes_file(session_id).read_text(encoding="utf-8")
    assert second_transcript.run_id == second_job.job_id
    assert second_transcript.run_id != first_transcript.run_id
    assert migrated.run_id == second_transcript.run_id
    assert migrated.speaker_names[0].display_name == "Morgan"
    assert migrated.segment_texts == ()
    assert "Morgan" in markdown
    assert "Remote reply" in markdown
    assert "Old correction" not in markdown
    assert len(factory.calls) == 2


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

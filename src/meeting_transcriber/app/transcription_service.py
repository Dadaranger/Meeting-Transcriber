from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Protocol
from uuid import UUID, uuid5

from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.domain.session import SessionState
from meeting_transcriber.domain.transcript import (
    TranscriptDocument,
    TranscriptionJob,
    TranscriptionJobState,
    TranscriptionProfile,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
    TranscriptWord,
)
from meeting_transcriber.processing.diarization_engine import (
    DiarizationCancelled,
    DiarizationRuntimeError,
)
from meeting_transcriber.processing.engine import (
    EngineSegment,
    FasterWhisperEngine,
    TranscriptionCancelled,
    TranscriptionEngine,
)
from meeting_transcriber.processing.markdown_export import render_meeting_notes
from meeting_transcriber.processing.preparation import (
    AudioPreparationService,
    PreparedAudioChunk,
    PreparedAudioPlan,
)
from meeting_transcriber.processing.remote_speaker_service import RemoteSpeakerService
from meeting_transcriber.storage.meeting_notes_store import MeetingNotesStore
from meeting_transcriber.storage.review_store import ReviewStore
from meeting_transcriber.storage.transcript_store import (
    TranscriptNotFoundError,
    TranscriptStore,
)


class TranscriptionWorkflowError(RuntimeError):
    """Raised when an offline transcription workflow cannot safely proceed."""


class AudioPreparer(Protocol):
    def prepare(self, session_directory: Path, run_id: str) -> PreparedAudioPlan: ...


class TranscriptionEngineFactory(Protocol):
    def __call__(
        self,
        profile: TranscriptionProfile,
        *,
        allow_download: bool,
    ) -> TranscriptionEngine: ...


class MeetingNotesWriter(Protocol):
    def save(self, session_id: str, run_id: str, markdown: str) -> Path: ...


class RemoteSpeakerProcessor(Protocol):
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
    ) -> TranscriptDocument: ...


class TranscriptionWorkflow(Protocol):
    @property
    def is_processing(self) -> bool: ...

    def start(
        self,
        session_id: str,
        *,
        profile: TranscriptionProfile,
        language: str | None,
        hotwords: str | None,
        allow_download: bool,
        separate_remote_speakers: bool = False,
        min_remote_speakers: int | None = None,
        max_remote_speakers: int | None = None,
        diarization_allow_download: bool = False,
        diarization_access_token: str | None = None,
    ) -> TranscriptionJob: ...

    def cancel(self) -> None: ...

    def current_job(self) -> TranscriptionJob | None: ...

    def job_for(self, session_id: str) -> TranscriptionJob | None: ...

    def recover_interrupted_jobs(self) -> list[TranscriptionJob]: ...


def _default_engine_factory(model_cache: Path) -> TranscriptionEngineFactory:
    def build(
        profile: TranscriptionProfile,
        *,
        allow_download: bool,
    ) -> TranscriptionEngine:
        return FasterWhisperEngine(
            profile,
            model_cache,
            allow_download=allow_download,
        )

    return build


class MeetingTranscriptionService:
    """Run persisted, cancellable local transcription work on one background thread."""

    def __init__(
        self,
        session_service: MeetingSessionService,
        transcript_store: TranscriptStore,
        model_cache: Path,
        *,
        preparer: AudioPreparer | None = None,
        engine_factory: TranscriptionEngineFactory | None = None,
        notes_store: MeetingNotesWriter | None = None,
        review_store: ReviewStore | None = None,
        remote_speaker_processor: RemoteSpeakerProcessor | None = None,
    ):
        self.session_service = session_service
        self.transcript_store = transcript_store
        self.preparer = preparer or AudioPreparationService()
        self.engine_factory = engine_factory or _default_engine_factory(model_cache)
        self.notes_store = notes_store or MeetingNotesStore(transcript_store.meeting_root)
        self.review_store = review_store or ReviewStore(transcript_store.meeting_root)
        self.remote_speaker_processor = remote_speaker_processor or RemoteSpeakerService(
            transcript_store.meeting_root,
            model_cache,
        )
        self._lock = Lock()
        self._cancel_event = Event()
        self._thread: Thread | None = None
        self._job: TranscriptionJob | None = None

    @property
    def is_processing(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def current_job(self) -> TranscriptionJob | None:
        with self._lock:
            return self._job

    def job_for(self, session_id: str) -> TranscriptionJob | None:
        current = self.current_job()
        if current is not None and current.session_id == session_id:
            return current
        try:
            return self.transcript_store.load_job(session_id)
        except TranscriptNotFoundError:
            return None

    def start(
        self,
        session_id: str,
        *,
        profile: TranscriptionProfile,
        language: str | None,
        hotwords: str | None,
        allow_download: bool,
        separate_remote_speakers: bool = False,
        min_remote_speakers: int | None = None,
        max_remote_speakers: int | None = None,
        diarization_allow_download: bool = False,
        diarization_access_token: str | None = None,
    ) -> TranscriptionJob:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise TranscriptionWorkflowError("Another meeting is already being transcribed")
        session = self.session_service.get_session(session_id)
        if session.state not in {SessionState.RECORDED, SessionState.READY, SessionState.EXPORTED}:
            raise TranscriptionWorkflowError("Only a completed recording can be transcribed")
        normalized_language = language.strip() if language and language.strip() else None
        job = self._new_or_retry_job(
            session_id,
            profile,
            normalized_language,
            separate_remote_speakers,
            min_remote_speakers,
            max_remote_speakers,
        )
        self.transcript_store.save_job(job)
        try:
            self.session_service.transition_state(session_id, SessionState.PROCESSING)
        except (OSError, ValueError) as error:
            raise TranscriptionWorkflowError(
                "Meeting processing state could not be saved"
            ) from error

        self._cancel_event = Event()
        worker = Thread(
            target=self._run,
            args=(
                job,
                hotwords.strip() if hotwords and hotwords.strip() else None,
                allow_download,
                diarization_allow_download,
                diarization_access_token,
            ),
            name=f"transcribe-{session_id}",
            daemon=True,
        )
        with self._lock:
            self._job = job
            self._thread = worker
        worker.start()
        return job

    def cancel(self) -> None:
        if not self.is_processing:
            raise TranscriptionWorkflowError("No transcription job is running")
        self._cancel_event.set()

    def wait(self, timeout_seconds: float = 30.0) -> TranscriptionJob:
        with self._lock:
            worker = self._thread
        if worker is None:
            raise TranscriptionWorkflowError("No transcription job has started")
        worker.join(timeout_seconds)
        if worker.is_alive():
            raise TimeoutError("Transcription job did not finish before the timeout")
        job = self.current_job()
        if job is None:
            raise TranscriptionWorkflowError("Transcription job state is unavailable")
        return job

    def recover_interrupted_jobs(self) -> list[TranscriptionJob]:
        recovered: list[TranscriptionJob] = []
        for session in self.session_service.recent_sessions():
            if session.state is not SessionState.PROCESSING:
                continue
            try:
                job = self.transcript_store.load_job(session.session_id)
            except TranscriptNotFoundError:
                self.session_service.transition_state(session.session_id, SessionState.RECORDED)
                continue
            if job.state in {
                TranscriptionJobState.PREPARING,
                TranscriptionJobState.TRANSCRIBING,
                TranscriptionJobState.DIARIZING,
            }:
                job = job.transition(
                    TranscriptionJobState.FAILED,
                    error="The application closed before transcription completed",
                )
                self.transcript_store.save_job(job)
                recovered.append(job)
            self.session_service.transition_state(session.session_id, SessionState.RECORDED)
        return recovered

    def _new_or_retry_job(
        self,
        session_id: str,
        profile: TranscriptionProfile,
        language: str | None,
        separate_remote_speakers: bool,
        min_remote_speakers: int | None,
        max_remote_speakers: int | None,
    ) -> TranscriptionJob:
        try:
            existing = self.transcript_store.load_job(session_id)
        except TranscriptNotFoundError:
            existing = None
        if (
            existing is not None
            and existing.state in {TranscriptionJobState.CANCELLED, TranscriptionJobState.FAILED}
            and existing.profile is profile
            and existing.language == language
            and existing.separate_remote_speakers is separate_remote_speakers
            and existing.min_remote_speakers == min_remote_speakers
            and existing.max_remote_speakers == max_remote_speakers
        ):
            return existing.retry()
        return TranscriptionJob.new(
            session_id,
            profile=profile,
            language=language,
            separate_remote_speakers=separate_remote_speakers,
            min_remote_speakers=min_remote_speakers,
            max_remote_speakers=max_remote_speakers,
        )

    def _run(
        self,
        job: TranscriptionJob,
        hotwords: str | None,
        allow_download: bool,
        diarization_allow_download: bool,
        diarization_access_token: str | None,
    ) -> None:
        current = job
        try:
            current = current.transition(TranscriptionJobState.PREPARING)
            self._persist_current(current)
            session_directory = self.session_service.session_directory(current.session_id)
            transcript = self._existing_transcript(current)
            plan: PreparedAudioPlan | None = None
            if transcript is not None:
                current = current.with_progress(transcript.duration_ms, transcript.duration_ms)
                current = current.transition(TranscriptionJobState.TRANSCRIBING)
                self._persist_current(current)
            else:
                plan = self.preparer.prepare(
                    session_directory,
                    current.job_id,
                )
                current = current.with_progress(0, plan.total_audio_ms)
                self._persist_current(current)
                engine = self.engine_factory(current.profile, allow_download=allow_download)

                def update_model_progress(downloaded_bytes: int, total_bytes: int) -> None:
                    nonlocal current
                    current = current.with_model_download_progress(
                        downloaded_bytes,
                        total_bytes,
                    )
                    self._persist_current(current)

                engine.prepare(
                    cancel_requested=self._cancel_event.is_set,
                    progress_callback=update_model_progress,
                )
                current = current.transition(TranscriptionJobState.TRANSCRIBING)
                self._persist_current(current)
                transcript = self._transcribe_plan(
                    current,
                    plan,
                    engine,
                    hotwords=hotwords,
                )
                current = self.current_job() or current
                self.transcript_store.save_transcript(transcript)
            if current.separate_remote_speakers and any(
                segment.source is TranscriptSource.SYSTEM_AUDIO for segment in transcript.segments
            ):
                if plan is None:
                    plan = self.preparer.prepare(
                        session_directory,
                        current.job_id,
                    )
                current = current.transition(TranscriptionJobState.DIARIZING)
                self._persist_current(current)
                try:
                    transcript = self.remote_speaker_processor.separate(
                        plan,
                        session_directory,
                        transcript,
                        allow_download=diarization_allow_download,
                        access_token=diarization_access_token,
                        min_speakers=current.min_remote_speakers,
                        max_speakers=current.max_remote_speakers,
                        cancel_requested=self._cancel_event.is_set,
                    )
                except DiarizationCancelled as error:
                    raise TranscriptionCancelled(str(error)) from error
                except DiarizationRuntimeError as error:
                    current = current.with_warning(
                        f"Remote-speaker separation was unavailable: {error}"
                    )
                    self._persist_current(current)
            self.transcript_store.save_transcript(transcript)
            self._save_meeting_notes(transcript)
            self.session_service.transition_state(current.session_id, SessionState.READY)
            current = current.transition(TranscriptionJobState.COMPLETED)
            self._persist_current(current)
        except TranscriptionCancelled:
            current = current.transition(TranscriptionJobState.CANCELLED)
            self._persist_current(current)
            self._restore_recorded_state(current.session_id)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            if current.state in {
                TranscriptionJobState.PREPARING,
                TranscriptionJobState.TRANSCRIBING,
                TranscriptionJobState.DIARIZING,
            }:
                current = current.transition(TranscriptionJobState.FAILED, error=message)
                with suppress(OSError):
                    self._persist_current(current)
            self._restore_recorded_state(current.session_id)
        finally:
            with self._lock:
                self._job = current

    def _transcribe_plan(
        self,
        current: TranscriptionJob,
        plan: PreparedAudioPlan,
        engine: TranscriptionEngine,
        *,
        hotwords: str | None,
    ) -> TranscriptDocument:
        segments: list[TranscriptSegment] = []
        language_scores: dict[str, float] = defaultdict(float)
        processed_ms = 0
        for chunk in plan.chunks:
            result = engine.transcribe_chunk(
                chunk,
                language=current.language,
                hotwords=hotwords,
                cancel_requested=self._cancel_event.is_set,
            )
            language_scores[result.language] += result.language_probability * chunk.duration_ms
            segments.extend(self._transcript_segments(current.job_id, chunk, result.segments))
            processed_ms += chunk.duration_ms
            current = current.with_progress(processed_ms, plan.total_audio_ms)
            self._persist_current(current)
        if self._cancel_event.is_set():
            raise TranscriptionCancelled("Transcription was cancelled")
        language = current.language or max(
            language_scores,
            key=lambda candidate: language_scores[candidate],
            default="und",
        )
        return TranscriptDocument.new(
            current.session_id,
            run_id=current.job_id,
            language=language,
            engine=engine.engine_name,
            model=engine.model_name,
            profile=current.profile,
            speakers=(
                TranscriptSpeaker("local", "You", TranscriptSource.MICROPHONE),
                TranscriptSpeaker(
                    "remote",
                    "Remote speakers",
                    TranscriptSource.SYSTEM_AUDIO,
                ),
            ),
            segments=tuple(
                sorted(
                    segments,
                    key=lambda segment: (
                        segment.start_ms,
                        segment.end_ms,
                        segment.segment_id,
                    ),
                )
            ),
        )

    def _persist_current(self, job: TranscriptionJob) -> None:
        self.transcript_store.save_job(job)
        with self._lock:
            self._job = job

    def _existing_transcript(self, job: TranscriptionJob) -> TranscriptDocument | None:
        try:
            return self.transcript_store.load_transcript(job.session_id, job.job_id)
        except TranscriptNotFoundError:
            return None

    def _save_meeting_notes(self, transcript: TranscriptDocument) -> Path:
        session = self.session_service.get_session(transcript.session_id)
        review = self.review_store.load_for_transcript(transcript)
        if review.revision > 0:
            self.review_store.save(review)
        markdown = render_meeting_notes(
            session,
            review.apply(transcript),
            review.structured_notes,
        )
        return self.notes_store.save(transcript.session_id, transcript.run_id, markdown)

    def _restore_recorded_state(self, session_id: str) -> None:
        with suppress(OSError, ValueError):
            session = self.session_service.get_session(session_id)
            if session.state is SessionState.PROCESSING:
                self.session_service.transition_state(session_id, SessionState.RECORDED)

    @staticmethod
    def _transcript_segments(
        job_id: str,
        chunk: PreparedAudioChunk,
        engine_segments: tuple[EngineSegment, ...],
    ) -> list[TranscriptSegment]:
        converted: list[TranscriptSegment] = []
        for index, raw_segment in enumerate(engine_segments):
            local_start = max(0, raw_segment.start_ms)
            local_end = min(chunk.duration_ms, raw_segment.end_ms)
            if local_end <= local_start:
                continue
            start_ms = chunk.timeline_start_ms + local_start
            end_ms = chunk.timeline_start_ms + local_end
            words: list[TranscriptWord] = []
            previous_end = start_ms
            for raw_word in raw_segment.words:
                word_start = max(start_ms, chunk.timeline_start_ms + raw_word.start_ms)
                word_end = min(end_ms, chunk.timeline_start_ms + raw_word.end_ms)
                word_start = max(word_start, previous_end)
                if word_end <= word_start:
                    continue
                words.append(
                    TranscriptWord(
                        raw_word.text,
                        word_start,
                        word_end,
                        raw_word.probability,
                    )
                )
                previous_end = word_end
            source_label = "local" if chunk.source is TranscriptSource.MICROPHONE else "remote"
            converted.append(
                TranscriptSegment(
                    segment_id=str(
                        uuid5(
                            UUID(job_id),
                            f"{chunk.source.value}:{chunk.sequence}:{index}",
                        )
                    ),
                    start_ms=start_ms,
                    end_ms=end_ms,
                    speaker_id=source_label,
                    text=raw_segment.text,
                    source=chunk.source,
                    confidence=raw_segment.confidence,
                    words=tuple(words),
                )
            )
        return converted

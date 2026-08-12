from __future__ import annotations

from collections import defaultdict
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
from meeting_transcriber.processing.engine import (
    EngineSegment,
    FasterWhisperEngine,
    TranscriptionCancelled,
    TranscriptionEngine,
)
from meeting_transcriber.processing.preparation import (
    AudioPreparationService,
    PreparedAudioChunk,
    PreparedAudioPlan,
)
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
    ):
        self.session_service = session_service
        self.transcript_store = transcript_store
        self.preparer = preparer or AudioPreparationService()
        self.engine_factory = engine_factory or _default_engine_factory(model_cache)
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
    ) -> TranscriptionJob:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise TranscriptionWorkflowError("Another meeting is already being transcribed")
        session = self.session_service.get_session(session_id)
        if session.state not in {SessionState.RECORDED, SessionState.READY, SessionState.EXPORTED}:
            raise TranscriptionWorkflowError("Only a completed recording can be transcribed")
        normalized_language = language.strip() if language and language.strip() else None
        job = self._new_or_retry_job(session_id, profile, normalized_language)
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
            args=(job, hotwords.strip() if hotwords and hotwords.strip() else None, allow_download),
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
        ):
            return existing.retry()
        return TranscriptionJob.new(session_id, profile=profile, language=language)

    def _run(self, job: TranscriptionJob, hotwords: str | None, allow_download: bool) -> None:
        current = job
        try:
            current = current.transition(TranscriptionJobState.PREPARING)
            self._persist_current(current)
            plan = self.preparer.prepare(
                self.session_service.session_directory(current.session_id),
                current.job_id,
            )
            current = current.with_progress(0, plan.total_audio_ms)
            current = current.transition(TranscriptionJobState.TRANSCRIBING)
            self._persist_current(current)
            engine = self.engine_factory(current.profile, allow_download=allow_download)
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
            transcript = TranscriptDocument.new(
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
            self.transcript_store.save_transcript(transcript)
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
            }:
                current = current.transition(TranscriptionJobState.FAILED, error=message)
                with suppress(OSError):
                    self._persist_current(current)
            self._restore_recorded_state(current.session_id)
        finally:
            with self._lock:
                self._job = current

    def _persist_current(self, job: TranscriptionJob) -> None:
        self.transcript_store.save_job(job)
        with self._lock:
            self._job = job

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

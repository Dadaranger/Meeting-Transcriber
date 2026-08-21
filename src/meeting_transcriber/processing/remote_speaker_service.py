from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from meeting_transcriber.domain.diarization import DiarizationDocument
from meeting_transcriber.domain.transcript import TranscriptDocument, TranscriptSource
from meeting_transcriber.processing.diarization_engine import (
    DiarizationCancelled,
    DiarizationModelManager,
    PyannoteDiarizationEngine,
    RemoteAudioTimelineBuilder,
)
from meeting_transcriber.processing.diarization_merge import merge_remote_speakers
from meeting_transcriber.processing.preparation import PreparedAudioPlan
from meeting_transcriber.storage.diarization_store import DiarizationStore


class DiarizationEngine(Protocol):
    def diarize(
        self,
        audio_path: Path,
        *,
        session_id: str,
        run_id: str,
        min_speakers: int | None,
        max_speakers: int | None,
        cancel_requested: Callable[[], bool],
    ) -> DiarizationDocument: ...


class DiarizationEngineFactory(Protocol):
    def __call__(self, model_directory: Path) -> DiarizationEngine: ...


class RemoteSpeakerService:
    """Run optional local diarization and merge it into system-audio transcript text."""

    def __init__(
        self,
        meeting_root: Path,
        model_root: Path,
        *,
        model_manager: DiarizationModelManager | None = None,
        timeline_builder: RemoteAudioTimelineBuilder | None = None,
        engine_factory: DiarizationEngineFactory = PyannoteDiarizationEngine,
        store: DiarizationStore | None = None,
    ):
        self.model_manager = model_manager or DiarizationModelManager(model_root)
        self.timeline_builder = timeline_builder or RemoteAudioTimelineBuilder()
        self.engine_factory = engine_factory
        self.store = store or DiarizationStore(meeting_root)

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
        if not any(
            segment.source is not TranscriptSource.MICROPHONE for segment in transcript.segments
        ):
            return transcript
        if cancel_requested():
            raise DiarizationCancelled("Remote-speaker separation was cancelled")
        model_directory = self.model_manager.ensure_available(
            allow_download=allow_download,
            access_token=access_token,
        )
        audio_path = self.timeline_builder.build(plan, session_directory)
        document = self.engine_factory(model_directory).diarize(
            audio_path,
            session_id=transcript.session_id,
            run_id=transcript.run_id,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            cancel_requested=cancel_requested,
        )
        self.store.save(document)
        return merge_remote_speakers(transcript, document)

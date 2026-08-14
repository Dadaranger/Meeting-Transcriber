from __future__ import annotations

import importlib
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from meeting_transcriber.domain.transcript import TranscriptionProfile
from meeting_transcriber.processing.preparation import PreparedAudioChunk


class TranscriptionEngineError(RuntimeError):
    """Raised when the local speech engine cannot load or transcribe audio."""


class TranscriptionDependencyUnavailable(TranscriptionEngineError):
    """Raised when the optional local transcription runtime is not installed."""


class TranscriptionCancelled(TranscriptionEngineError):
    """Raised when transcription cooperatively stops between generated segments."""


@dataclass(frozen=True, slots=True)
class TranscriptionModelProfile:
    model_name: str
    beam_size: int


MODEL_PROFILES: Mapping[TranscriptionProfile, TranscriptionModelProfile] = {
    TranscriptionProfile.FAST: TranscriptionModelProfile("small", 1),
    TranscriptionProfile.BALANCED: TranscriptionModelProfile("medium", 5),
    TranscriptionProfile.ACCURATE: TranscriptionModelProfile("large-v3", 5),
}

STANDARD_VAD_PARAMETERS: Mapping[str, object] = {
    "min_silence_duration_ms": 500,
}
LOW_VOLUME_VAD_PARAMETERS: Mapping[str, object] = {
    "threshold": 0.15,
    "min_speech_duration_ms": 250,
    "min_silence_duration_ms": 500,
    "speech_pad_ms": 400,
}
LOW_VOLUME_FALLBACK_MINIMUM_MS = 1_000


@dataclass(frozen=True, slots=True)
class EngineWord:
    text: str
    start_ms: int
    end_ms: int
    probability: float | None


@dataclass(frozen=True, slots=True)
class EngineSegment:
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None
    words: tuple[EngineWord, ...]


@dataclass(frozen=True, slots=True)
class ChunkTranscription:
    language: str
    language_probability: float
    segments: tuple[EngineSegment, ...]


class TranscriptionEngine(Protocol):
    @property
    def engine_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def prepare(
        self,
        *,
        cancel_requested: Callable[[], bool],
        progress_callback: Callable[[int, int], None],
    ) -> None: ...

    def transcribe_chunk(
        self,
        chunk: PreparedAudioChunk,
        *,
        language: str | None,
        hotwords: str | None,
        cancel_requested: Callable[[], bool],
    ) -> ChunkTranscription: ...


class _WordResult(Protocol):
    word: str
    start: float
    end: float
    probability: float


class _SegmentResult(Protocol):
    text: str
    start: float
    end: float
    avg_logprob: float
    words: Iterable[_WordResult] | None


class _TranscriptionInfo(Protocol):
    language: str
    language_probability: float


class _WhisperModel(Protocol):
    def transcribe(
        self, audio: str, **options: object
    ) -> tuple[Iterable[_SegmentResult], _TranscriptionInfo]: ...


class WhisperModelFactory(Protocol):
    def __call__(
        self,
        model_name: str,
        *,
        device: str,
        compute_type: str,
        download_root: str,
        local_files_only: bool,
    ) -> object: ...


class ModelManager(Protocol):
    def ensure_available(
        self,
        model_name: str,
        *,
        cancel_requested: Callable[[], bool],
        progress_callback: Callable[[int, int], None],
    ) -> object: ...


def _load_whisper_model(
    model_name: str,
    *,
    device: str,
    compute_type: str,
    download_root: str,
    local_files_only: bool,
) -> _WhisperModel:
    try:
        module = importlib.import_module("faster_whisper")
        model_factory = cast(WhisperModelFactory, module.WhisperModel)
    except (ImportError, AttributeError) as error:
        raise TranscriptionDependencyUnavailable(
            "Install the transcription extra before loading a local speech model"
        ) from error
    return cast(
        _WhisperModel,
        model_factory(
            model_name,
            device=device,
            compute_type=compute_type,
            download_root=download_root,
            local_files_only=local_files_only,
        ),
    )


class FasterWhisperEngine:
    """Lazy local faster-whisper adapter with explicit model acquisition."""

    def __init__(
        self,
        profile: TranscriptionProfile,
        model_cache: Path,
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        allow_download: bool = False,
        model_factory: WhisperModelFactory = _load_whisper_model,
        model_manager: ModelManager | None = None,
    ):
        self.profile = profile
        self.profile_settings = MODEL_PROFILES[profile]
        self.model_cache = model_cache
        self.device = device
        self.compute_type = compute_type
        self.allow_download = allow_download
        self.model_factory = model_factory
        self.model_manager = model_manager
        self._model: _WhisperModel | None = None
        self._model_acquired = False

    @property
    def engine_name(self) -> str:
        return "faster-whisper"

    @property
    def model_name(self) -> str:
        return self.profile_settings.model_name

    def prepare(
        self,
        *,
        cancel_requested: Callable[[], bool],
        progress_callback: Callable[[int, int], None],
    ) -> None:
        if cancel_requested():
            raise TranscriptionCancelled("Transcription was cancelled")
        if self.allow_download and not self._model_acquired:
            try:
                self._get_model()
            except TranscriptionDependencyUnavailable:
                raise
            except TranscriptionEngineError:
                pass
            else:
                self._model_acquired = True
                return
            from meeting_transcriber.processing.model_download import (
                ModelDownloadCancelled,
                ModelDownloadDependencyUnavailable,
                ModelDownloadError,
                TranscriptionModelManager,
            )

            manager = self.model_manager or TranscriptionModelManager(self.model_cache)
            try:
                manager.ensure_available(
                    self.model_name,
                    cancel_requested=cancel_requested,
                    progress_callback=progress_callback,
                )
            except ModelDownloadCancelled as error:
                raise TranscriptionCancelled(str(error)) from error
            except ModelDownloadDependencyUnavailable as error:
                raise TranscriptionDependencyUnavailable(str(error)) from error
            except ModelDownloadError as error:
                raise TranscriptionEngineError(str(error)) from error
            self._model_acquired = True
        self._get_model()

    def transcribe_chunk(
        self,
        chunk: PreparedAudioChunk,
        *,
        language: str | None,
        hotwords: str | None,
        cancel_requested: Callable[[], bool],
    ) -> ChunkTranscription:
        if cancel_requested():
            raise TranscriptionCancelled("Transcription was cancelled")
        self.prepare(
            cancel_requested=cancel_requested,
            progress_callback=lambda _downloaded, _total: None,
        )
        model = self._get_model()
        try:
            generated, info = model.transcribe(
                str(chunk.path),
                language=language,
                task="transcribe",
                beam_size=self.profile_settings.beam_size,
                word_timestamps=True,
                vad_filter=True,
                vad_parameters=dict(STANDARD_VAD_PARAMETERS),
                condition_on_previous_text=False,
                hallucination_silence_threshold=2.0,
                hotwords=hotwords,
            )
            segments = self._converted_segments(generated, cancel_requested)
            if not segments and chunk.duration_ms >= LOW_VOLUME_FALLBACK_MINIMUM_MS:
                generated, info = model.transcribe(
                    str(chunk.path),
                    language=language,
                    task="transcribe",
                    beam_size=self.profile_settings.beam_size,
                    word_timestamps=True,
                    vad_filter=True,
                    vad_parameters=dict(LOW_VOLUME_VAD_PARAMETERS),
                    condition_on_previous_text=False,
                    hallucination_silence_threshold=2.0,
                    hotwords=hotwords,
                )
                segments = self._converted_segments(generated, cancel_requested)
        except TranscriptionCancelled:
            raise
        except Exception as error:
            raise TranscriptionEngineError(
                f"Local model {self.model_name} could not transcribe {chunk.path.name}"
            ) from error
        return ChunkTranscription(
            language=info.language,
            language_probability=max(0.0, min(1.0, info.language_probability)),
            segments=tuple(segments),
        )

    @classmethod
    def _converted_segments(
        cls,
        generated: Iterable[_SegmentResult],
        cancel_requested: Callable[[], bool],
    ) -> list[EngineSegment]:
        segments: list[EngineSegment] = []
        for generated_segment in generated:
            if cancel_requested():
                raise TranscriptionCancelled("Transcription was cancelled")
            segment = cls._convert_segment(generated_segment)
            if segment is not None:
                segments.append(segment)
        return segments

    def _get_model(self) -> _WhisperModel:
        if self._model is not None:
            return self._model
        self.model_cache.mkdir(parents=True, exist_ok=True)
        try:
            self._model = cast(
                _WhisperModel,
                self.model_factory(
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=str(self.model_cache),
                    local_files_only=True,
                ),
            )
        except TranscriptionDependencyUnavailable:
            raise
        except Exception as error:
            raise TranscriptionEngineError(
                f"Could not load transcription model {self.model_name} from the local cache"
            ) from error
        return self._model

    @staticmethod
    def _convert_segment(segment: _SegmentResult) -> EngineSegment | None:
        text = segment.text.strip()
        if not text or segment.end <= segment.start:
            return None
        words: list[EngineWord] = []
        for word in segment.words or ():
            word_text = word.word.strip()
            if not word_text or word.end <= word.start:
                continue
            words.append(
                EngineWord(
                    text=word_text,
                    start_ms=round(word.start * 1_000),
                    end_ms=round(word.end * 1_000),
                    probability=max(0.0, min(1.0, word.probability)),
                )
            )
        confidence = (
            sum(word.probability for word in words if word.probability is not None) / len(words)
            if words
            else max(0.0, min(1.0, math.exp(min(0.0, segment.avg_logprob))))
        )
        return EngineSegment(
            text=text,
            start_ms=round(segment.start * 1_000),
            end_ms=round(segment.end * 1_000),
            confidence=confidence,
            words=tuple(words),
        )

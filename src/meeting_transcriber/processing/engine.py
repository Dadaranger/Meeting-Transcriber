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
    """Lazy local faster-whisper adapter with no model download during construction."""

    def __init__(
        self,
        profile: TranscriptionProfile,
        model_cache: Path,
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        allow_download: bool = False,
        model_factory: WhisperModelFactory = _load_whisper_model,
    ):
        self.profile = profile
        self.profile_settings = MODEL_PROFILES[profile]
        self.model_cache = model_cache
        self.device = device
        self.compute_type = compute_type
        self.allow_download = allow_download
        self.model_factory = model_factory
        self._model: _WhisperModel | None = None

    @property
    def engine_name(self) -> str:
        return "faster-whisper"

    @property
    def model_name(self) -> str:
        return self.profile_settings.model_name

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
        model = self._get_model()
        try:
            generated, info = model.transcribe(
                str(chunk.path),
                language=language,
                task="transcribe",
                beam_size=self.profile_settings.beam_size,
                word_timestamps=True,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,
                hallucination_silence_threshold=2.0,
                hotwords=hotwords,
            )
            segments: list[EngineSegment] = []
            for generated_segment in generated:
                if cancel_requested():
                    raise TranscriptionCancelled("Transcription was cancelled")
                segment = self._convert_segment(generated_segment)
                if segment is not None:
                    segments.append(segment)
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
                    local_files_only=not self.allow_download,
                ),
            )
        except TranscriptionDependencyUnavailable:
            raise
        except Exception as error:
            action = "download or load" if self.allow_download else "load from the local cache"
            raise TranscriptionEngineError(
                f"Could not {action} transcription model {self.model_name}"
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

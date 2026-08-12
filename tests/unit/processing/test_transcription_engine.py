from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from meeting_transcriber.domain.transcript import TranscriptionProfile, TranscriptSource
from meeting_transcriber.processing.engine import (
    MODEL_PROFILES,
    FasterWhisperEngine,
    TranscriptionCancelled,
    TranscriptionDependencyUnavailable,
    TranscriptionEngineError,
    _load_whisper_model,
)
from meeting_transcriber.processing.preparation import PreparedAudioChunk


@dataclass(frozen=True)
class FakeWord:
    word: str
    start: float
    end: float
    probability: float


@dataclass(frozen=True)
class FakeSegment:
    text: str
    start: float
    end: float
    avg_logprob: float
    words: tuple[FakeWord, ...] | None


@dataclass(frozen=True)
class FakeInfo:
    language: str
    language_probability: float


class FakeModel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def transcribe(
        self,
        audio: str,
        **options: object,
    ) -> tuple[tuple[FakeSegment, ...], FakeInfo]:
        self.calls.append((audio, options))
        return (
            (
                FakeSegment(
                    text=" Hello world. ",
                    start=0.1,
                    end=1.2,
                    avg_logprob=-0.2,
                    words=(
                        FakeWord(" Hello", 0.1, 0.5, 0.9),
                        FakeWord(" world.", 0.6, 1.2, 0.8),
                    ),
                ),
            ),
            FakeInfo("en", 0.96),
        )


class FakeModelFactory:
    def __init__(self, model: FakeModel, *, fail: bool = False, failures: int = 0):
        self.model = model
        self.fail = fail
        self.failures = failures
        self.calls: list[tuple[str, str, str, str, bool]] = []

    def __call__(
        self,
        model_name: str,
        *,
        device: str,
        compute_type: str,
        download_root: str,
        local_files_only: bool,
    ) -> FakeModel:
        self.calls.append((model_name, device, compute_type, download_root, local_files_only))
        if self.fail or self.failures:
            self.failures = max(0, self.failures - 1)
            raise OSError("Synthetic model cache miss")
        return self.model


class FakeModelManager:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def ensure_available(
        self,
        model_name: str,
        *,
        cancel_requested: object,
        progress_callback: object,
    ) -> object:
        assert callable(cancel_requested)
        assert callable(progress_callback)
        progress_callback(10, 20)
        self.calls.append(model_name)
        return object()


def _chunk(tmp_path: Path) -> PreparedAudioChunk:
    return PreparedAudioChunk(
        source=TranscriptSource.MICROPHONE,
        sequence=1,
        path=tmp_path / "microphone_0001.wav",
        timeline_start_ms=500,
        duration_ms=2_000,
        frame_count=32_000,
    )


def test_engine_profiles_make_accuracy_tradeoff_explicit() -> None:
    assert MODEL_PROFILES[TranscriptionProfile.FAST].model_name == "small"
    assert MODEL_PROFILES[TranscriptionProfile.BALANCED].model_name == "medium"
    assert MODEL_PROFILES[TranscriptionProfile.ACCURATE].model_name == "large-v3"
    assert MODEL_PROFILES[TranscriptionProfile.FAST].beam_size == 1


def test_faster_whisper_adapter_is_lazy_local_and_returns_word_evidence(tmp_path: Path) -> None:
    model = FakeModel()
    factory = FakeModelFactory(model)
    engine = FasterWhisperEngine(
        TranscriptionProfile.BALANCED,
        tmp_path / "models",
        model_factory=factory,
    )
    assert factory.calls == []

    result = engine.transcribe_chunk(
        _chunk(tmp_path),
        language="en",
        hotwords="Codex, WASAPI",
        cancel_requested=lambda: False,
    )

    assert factory.calls == [("medium", "cpu", "int8", str(tmp_path / "models"), True)]
    assert result.language == "en"
    assert result.language_probability == 0.96
    assert result.segments[0].text == "Hello world."
    assert result.segments[0].start_ms == 100
    assert result.segments[0].confidence == pytest.approx(0.85)
    assert [word.text for word in result.segments[0].words] == ["Hello", "world."]
    options = model.calls[0][1]
    assert options["vad_filter"] is True
    assert options["word_timestamps"] is True
    assert options["condition_on_previous_text"] is False
    assert options["hotwords"] == "Codex, WASAPI"

    engine.transcribe_chunk(
        _chunk(tmp_path),
        language="en",
        hotwords=None,
        cancel_requested=lambda: False,
    )
    assert len(factory.calls) == 1


def test_engine_checks_cancellation_before_loading_a_model(tmp_path: Path) -> None:
    factory = FakeModelFactory(FakeModel())
    engine = FasterWhisperEngine(
        TranscriptionProfile.FAST,
        tmp_path / "models",
        model_factory=factory,
    )

    with pytest.raises(TranscriptionCancelled):
        engine.transcribe_chunk(
            _chunk(tmp_path),
            language=None,
            hotwords=None,
            cancel_requested=lambda: True,
        )

    assert factory.calls == []


def test_engine_acquires_download_then_loads_strictly_from_local_cache(tmp_path: Path) -> None:
    factory = FakeModelFactory(FakeModel(), failures=1)
    manager = FakeModelManager()
    progress: list[tuple[int, int]] = []
    engine = FasterWhisperEngine(
        TranscriptionProfile.FAST,
        tmp_path / "models",
        allow_download=True,
        model_factory=factory,
        model_manager=manager,
    )

    engine.prepare(
        cancel_requested=lambda: False,
        progress_callback=lambda downloaded, total: progress.append((downloaded, total)),
    )

    assert manager.calls == ["small"]
    assert progress == [(10, 20)]
    assert factory.calls == [
        ("small", "cpu", "int8", str(tmp_path / "models"), True),
        ("small", "cpu", "int8", str(tmp_path / "models"), True),
    ]


def test_engine_uses_cached_model_without_network_metadata_check(tmp_path: Path) -> None:
    factory = FakeModelFactory(FakeModel())
    manager = FakeModelManager()
    engine = FasterWhisperEngine(
        TranscriptionProfile.FAST,
        tmp_path / "models",
        allow_download=True,
        model_factory=factory,
        model_manager=manager,
    )

    engine.prepare(
        cancel_requested=lambda: False,
        progress_callback=lambda _downloaded, _total: None,
    )

    assert manager.calls == []
    assert factory.calls == [("small", "cpu", "int8", str(tmp_path / "models"), True)]


def test_engine_reports_local_model_cache_failure(tmp_path: Path) -> None:
    engine = FasterWhisperEngine(
        TranscriptionProfile.ACCURATE,
        tmp_path / "models",
        model_factory=FakeModelFactory(FakeModel(), fail=True),
    )

    with pytest.raises(TranscriptionEngineError, match="local cache"):
        engine.transcribe_chunk(
            _chunk(tmp_path),
            language=None,
            hotwords=None,
            cancel_requested=lambda: False,
        )


def test_default_model_loader_reports_missing_optional_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_module(_name: str) -> object:
        raise ModuleNotFoundError("Synthetic missing optional runtime")

    monkeypatch.setattr(
        "meeting_transcriber.processing.engine.importlib.import_module", missing_module
    )

    with pytest.raises(TranscriptionDependencyUnavailable, match="transcription extra"):
        _load_whisper_model(
            "small",
            device="cpu",
            compute_type="int8",
            download_root="models",
            local_files_only=True,
        )

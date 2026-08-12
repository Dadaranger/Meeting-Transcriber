from __future__ import annotations

import wave
from pathlib import Path

import pytest

from meeting_transcriber.domain.transcript import TranscriptSource
from meeting_transcriber.processing.diarization_engine import (
    PYANNOTE_REPOSITORY,
    PYANNOTE_REVISION,
    DiarizationCancelled,
    DiarizationModelManager,
    DiarizationSetupError,
    PyannoteDiarizationEngine,
    RemoteAudioTimelineBuilder,
    _load_waveform_input,
)
from meeting_transcriber.processing.preparation import PreparedAudioChunk, PreparedAudioPlan

SESSION_ID = "1d7ee70c-7c04-4d17-903b-7b55152ea495"
RUN_ID = "66c62603-793c-4940-b76b-4b5355347144"


def _write_wav(path: Path, frames: int, sample: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(sample.to_bytes(2, "little", signed=True) * frames)


def test_timeline_builder_inserts_gaps_and_reuses_valid_output(tmp_path: Path) -> None:
    session_directory = tmp_path / SESSION_ID
    first_path = tmp_path / "first.wav"
    second_path = tmp_path / "second.wav"
    _write_wav(first_path, 160, 1_000)
    _write_wav(second_path, 160, 2_000)
    plan = PreparedAudioPlan(
        SESSION_ID,
        RUN_ID,
        (
            PreparedAudioChunk(
                TranscriptSource.SYSTEM_AUDIO,
                1,
                first_path,
                0,
                10,
                160,
            ),
            PreparedAudioChunk(
                TranscriptSource.SYSTEM_AUDIO,
                2,
                second_path,
                20,
                10,
                160,
            ),
        ),
        20,
        30,
    )
    builder = RemoteAudioTimelineBuilder()

    output = builder.build(plan, session_directory)
    modified = output.stat().st_mtime_ns
    reused = builder.build(plan, session_directory)

    assert reused == output
    assert reused.stat().st_mtime_ns == modified
    with wave.open(str(output), "rb") as audio:
        samples = [
            int.from_bytes(audio.readframes(1), "little", signed=True) for _index in range(480)
        ]
    assert set(samples[:160]) == {1_000}
    assert set(samples[160:320]) == {0}
    assert set(samples[320:]) == {2_000}


def test_model_manager_requires_explicit_download_and_temporary_token(tmp_path: Path) -> None:
    manager = DiarizationModelManager(tmp_path)

    with pytest.raises(DiarizationSetupError, match="not cached"):
        manager.ensure_available(allow_download=False, access_token=None)
    with pytest.raises(DiarizationSetupError, match="temporary Hugging Face"):
        manager.ensure_available(allow_download=True, access_token=None)


def test_model_manager_downloads_gated_snapshot_to_local_directory(tmp_path: Path) -> None:
    calls: list[tuple[str, Path, str, str]] = []

    def download(repo_id: str, *, local_dir: Path, token: str, revision: str) -> str:
        calls.append((repo_id, local_dir, token, revision))
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "config.yaml").write_text("pipeline: fixture", encoding="utf-8")
        return str(local_dir)

    manager = DiarizationModelManager(tmp_path, snapshot_downloader=download)

    model_directory = manager.ensure_available(
        allow_download=True,
        access_token="temporary-token",
    )

    assert manager.is_available
    assert calls == [(PYANNOTE_REPOSITORY, model_directory, "temporary-token", PYANNOTE_REVISION)]


def test_incomplete_model_directory_is_never_treated_as_cached(tmp_path: Path) -> None:
    manager = DiarizationModelManager(tmp_path)
    manager.model_directory.mkdir()
    (manager.model_directory / "config.yaml").write_text("pipeline: partial", encoding="utf-8")

    assert not manager.is_available
    with pytest.raises(DiarizationSetupError, match="not cached"):
        manager.ensure_available(allow_download=False, access_token=None)


class FakeSegment:
    def __init__(self, start: float, end: float):
        self.start = start
        self.end = end


class FakeAnnotation:
    def itertracks(self, *, yield_label: bool):  # type: ignore[no-untyped-def]
        assert yield_label
        return iter(
            (
                (FakeSegment(0.1, 0.5), object(), "SPEAKER_01"),
                (FakeSegment(0.6, 1.0), object(), "SPEAKER_00"),
                (FakeSegment(1.0, 1.4), object(), "SPEAKER_00"),
            )
        )


class FakeOutput:
    exclusive_speaker_diarization = FakeAnnotation()


class FakePipeline:
    def __init__(self) -> None:
        self.device: object | None = None
        self.calls: list[tuple[object, int | None, int | None]] = []

    def to(self, device: object) -> object:
        self.device = device
        return self

    def __call__(
        self,
        audio: object,
        *,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> FakeOutput:
        self.calls.append((audio, min_speakers, max_speakers))
        return FakeOutput()


def test_pyannote_adapter_uses_exclusive_turns_and_stable_first_seen_labels(
    tmp_path: Path,
) -> None:
    pipeline = FakePipeline()
    engine = PyannoteDiarizationEngine(
        tmp_path / "model",
        pipeline_loader=lambda _path: pipeline,
        device_factory=lambda: "cpu",
        waveform_loader=lambda path: {"waveform": path.name, "sample_rate": 16_000},
    )

    document = engine.diarize(
        tmp_path / "remote.wav",
        session_id=SESSION_ID,
        run_id=RUN_ID,
        min_speakers=2,
        max_speakers=4,
        cancel_requested=lambda: False,
    )

    assert [(turn.start_ms, turn.end_ms, turn.speaker_id) for turn in document.turns] == [
        (100, 500, "remote-1"),
        (600, 1_400, "remote-2"),
    ]
    assert pipeline.device == "cpu"
    assert pipeline.calls == [({"waveform": "remote.wav", "sample_rate": 16_000}, 2, 4)]


def test_waveform_input_preloads_normalized_pcm_without_audio_decoder(tmp_path: Path) -> None:
    audio_path = tmp_path / "remote.wav"
    _write_wav(audio_path, 3, 1_000)

    result = _load_waveform_input(
        audio_path,
        tensor_factory=lambda pcm: bytes(pcm),
    )

    assert result == {
        "waveform": (1_000).to_bytes(2, "little", signed=True) * 3,
        "sample_rate": 16_000,
    }


def test_pyannote_adapter_honors_cancellation_before_loading(tmp_path: Path) -> None:
    engine = PyannoteDiarizationEngine(
        tmp_path,
        pipeline_loader=lambda _path: pytest.fail("pipeline should not load"),
    )

    with pytest.raises(DiarizationCancelled):
        engine.diarize(
            tmp_path / "remote.wav",
            session_id=SESSION_ID,
            run_id=RUN_ID,
            min_speakers=None,
            max_speakers=None,
            cancel_requested=lambda: True,
        )

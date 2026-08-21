from __future__ import annotations

import importlib
import json
import os
import wave
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

from meeting_transcriber.domain.diarization import DiarizationDocument, DiarizationTurn
from meeting_transcriber.domain.transcript import TranscriptSource
from meeting_transcriber.processing.preparation import (
    TARGET_CHANNELS,
    TARGET_SAMPLE_RATE,
    TARGET_SAMPLE_WIDTH_BYTES,
    PreparedAudioPlan,
)

PYANNOTE_REPOSITORY = "pyannote/speaker-diarization-community-1"
PYANNOTE_REVISION = "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee"
PYANNOTE_MODEL_DIRECTORY = "pyannote-speaker-diarization-community-1"
PYANNOTE_MODEL_MARKER = ".meeting-transcriber-model.json"


class DiarizationRuntimeError(RuntimeError):
    """Raised when local remote-speaker separation cannot complete."""


class DiarizationSetupError(DiarizationRuntimeError):
    """Raised when the optional gated model or runtime is unavailable."""


class DiarizationCancelled(DiarizationRuntimeError):
    """Raised when diarization is cancelled at a safe model boundary."""


class _HubSnapshotDownloader(Protocol):
    def __call__(
        self,
        repo_id: str,
        *,
        local_dir: Path,
        token: str,
        revision: str,
    ) -> str: ...


class _TimelineSegment(Protocol):
    start: float
    end: float


class _Annotation(Protocol):
    def itertracks(
        self,
        *,
        yield_label: bool,
    ) -> Iterable[tuple[_TimelineSegment, object, str]]: ...


class _PipelineOutput(Protocol):
    exclusive_speaker_diarization: _Annotation


class _Pipeline(Protocol):
    def to(self, device: object) -> object: ...

    def __call__(
        self,
        audio: object,
        *,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> object: ...


class _PipelineClass(Protocol):
    def from_pretrained(self, model_path: str) -> _Pipeline | None: ...


class _Tensor(Protocol):
    def reshape(self, *shape: int) -> _Tensor: ...

    def to(self, *, dtype: object) -> _Tensor: ...

    def __truediv__(self, value: float) -> _Tensor: ...


class _TorchModule(Protocol):
    int16: object
    float32: object

    def frombuffer(self, buffer: bytearray, *, dtype: object) -> _Tensor: ...


def _snapshot_download(
    repo_id: str,
    *,
    local_dir: Path,
    token: str,
    revision: str,
) -> str:
    try:
        module = importlib.import_module("huggingface_hub")
        downloader = cast(_HubSnapshotDownloader, module.snapshot_download)
    except (ImportError, AttributeError) as error:
        raise DiarizationSetupError("The optional diarization runtime is not installed") from error
    return downloader(repo_id, local_dir=local_dir, token=token, revision=revision)


@dataclass(slots=True)
class DiarizationModelManager:
    model_root: Path
    snapshot_downloader: _HubSnapshotDownloader = _snapshot_download

    @property
    def model_directory(self) -> Path:
        return self.model_root / PYANNOTE_MODEL_DIRECTORY

    @property
    def is_available(self) -> bool:
        return self._has_complete_model()

    def _has_complete_model(self) -> bool:
        marker = self.model_directory / PYANNOTE_MODEL_MARKER
        if not (self.model_directory / "config.yaml").is_file() or not marker.is_file():
            return False
        try:
            document: object = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return document == {"repository": PYANNOTE_REPOSITORY, "revision": PYANNOTE_REVISION}

    def ensure_available(
        self,
        *,
        allow_download: bool,
        access_token: str | None,
    ) -> Path:
        if self.is_available:
            return self.model_directory
        if not allow_download:
            raise DiarizationSetupError(
                "Remote-speaker model is not cached. Enable its explicit one-time download."
            )
        token = access_token.strip() if access_token else ""
        if not token:
            raise DiarizationSetupError(
                "A temporary Hugging Face access token is required for the gated community-1 model"
            )
        self.model_directory.mkdir(parents=True, exist_ok=True)
        try:
            self.snapshot_downloader(
                PYANNOTE_REPOSITORY,
                local_dir=self.model_directory,
                token=token,
                revision=PYANNOTE_REVISION,
            )
        except Exception as error:
            raise DiarizationSetupError(
                "The remote-speaker model download failed. Confirm that its Hugging Face "
                "access conditions were accepted and the token has read permission."
            ) from error
        if not (self.model_directory / "config.yaml").is_file():
            raise DiarizationSetupError("The downloaded remote-speaker model is incomplete")
        marker = self.model_directory / PYANNOTE_MODEL_MARKER
        temporary_marker = marker.with_suffix(".tmp")
        try:
            temporary_marker.write_text(
                json.dumps(
                    {"repository": PYANNOTE_REPOSITORY, "revision": PYANNOTE_REVISION},
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_marker, marker)
        except OSError as error:
            raise DiarizationSetupError(
                "The downloaded remote-speaker model could not be finalized"
            ) from error
        finally:
            temporary_marker.unlink(missing_ok=True)
        return self.model_directory


class RemoteAudioTimelineBuilder:
    """Assemble normalized system-audio chunks on their original meeting timeline."""

    def build(self, plan: PreparedAudioPlan, session_directory: Path) -> Path:
        remote_chunks = tuple(
            chunk for chunk in plan.chunks if chunk.source is not TranscriptSource.MICROPHONE
        )
        if not remote_chunks:
            raise DiarizationRuntimeError("No prepared remote or imported audio is available")
        output = (
            session_directory
            / "derived"
            / "transcription"
            / str(UUID(plan.run_id))
            / "remote-timeline.wav"
        )
        expected_frames = max(
            round(chunk.timeline_start_ms * TARGET_SAMPLE_RATE / 1_000) + chunk.frame_count
            for chunk in remote_chunks
        )
        if output.is_file() and _valid_timeline(output, expected_frames):
            return output

        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".wav.tmp")
        temporary.unlink(missing_ok=True)
        try:
            with wave.open(str(temporary), "wb") as destination:
                destination.setnchannels(TARGET_CHANNELS)
                destination.setsampwidth(TARGET_SAMPLE_WIDTH_BYTES)
                destination.setframerate(TARGET_SAMPLE_RATE)
                written_frames = 0
                for chunk in remote_chunks:
                    start_frame = round(chunk.timeline_start_ms * TARGET_SAMPLE_RATE / 1_000)
                    if start_frame < written_frames:
                        raise DiarizationRuntimeError(
                            "Prepared system-audio chunks overlap on the meeting timeline"
                        )
                    _write_silence(destination, start_frame - written_frames)
                    try:
                        with wave.open(str(chunk.path), "rb") as source:
                            if (
                                source.getframerate() != TARGET_SAMPLE_RATE
                                or source.getnchannels() != TARGET_CHANNELS
                                or source.getsampwidth() != TARGET_SAMPLE_WIDTH_BYTES
                                or source.getnframes() != chunk.frame_count
                            ):
                                raise DiarizationRuntimeError(
                                    f"Prepared system-audio chunk is invalid: {chunk.path.name}"
                                )
                            destination.writeframes(source.readframes(chunk.frame_count))
                    except (OSError, EOFError, wave.Error) as error:
                        raise DiarizationRuntimeError(
                            f"Prepared system-audio chunk could not be read: {chunk.path.name}"
                        ) from error
                    written_frames = start_frame + chunk.frame_count
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return output


def _valid_timeline(path: Path, expected_frames: int) -> bool:
    try:
        with wave.open(str(path), "rb") as audio:
            return (
                audio.getframerate() == TARGET_SAMPLE_RATE
                and audio.getnchannels() == TARGET_CHANNELS
                and audio.getsampwidth() == TARGET_SAMPLE_WIDTH_BYTES
                and audio.getnframes() == expected_frames
            )
    except (OSError, EOFError, wave.Error):
        return False


def _write_silence(destination: wave.Wave_write, frame_count: int) -> None:
    silence = bytes(64 * 1_024)
    byte_count = frame_count * TARGET_SAMPLE_WIDTH_BYTES
    while byte_count:
        block_size = min(byte_count, len(silence))
        destination.writeframesraw(silence[:block_size])
        byte_count -= block_size


def _default_pipeline_loader(model_directory: Path) -> _Pipeline:
    os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
    try:
        module = importlib.import_module("pyannote.audio")
        pipeline_class = cast(_PipelineClass, module.Pipeline)
        pipeline = pipeline_class.from_pretrained(str(model_directory))
    except (ImportError, AttributeError, OSError) as error:
        raise DiarizationSetupError(
            "The optional pyannote.audio runtime could not load the local model"
        ) from error
    if pipeline is None:
        raise DiarizationSetupError("The local pyannote.audio model could not be loaded")
    return pipeline


def _default_device() -> object:
    try:
        torch = importlib.import_module("torch")
        cuda = torch.cuda
        device_factory = torch.device
        return device_factory("cuda" if cuda.is_available() else "cpu")
    except (ImportError, AttributeError) as error:
        raise DiarizationSetupError("The optional PyTorch runtime is unavailable") from error


def _torch_tensor(pcm: bytearray) -> _Tensor:
    try:
        module = cast(_TorchModule, importlib.import_module("torch"))
        return (
            module.frombuffer(pcm, dtype=module.int16).reshape(1, -1).to(dtype=module.float32)
            / 32_768.0
        )
    except (ImportError, AttributeError, RuntimeError) as error:
        raise DiarizationSetupError("The optional PyTorch runtime is unavailable") from error


def _load_waveform_input(
    audio_path: Path,
    *,
    tensor_factory: Callable[[bytearray], object] = _torch_tensor,
) -> dict[str, object]:
    try:
        with wave.open(str(audio_path), "rb") as audio:
            if (
                audio.getframerate() != TARGET_SAMPLE_RATE
                or audio.getnchannels() != TARGET_CHANNELS
                or audio.getsampwidth() != TARGET_SAMPLE_WIDTH_BYTES
            ):
                raise DiarizationRuntimeError("Remote-speaker timeline has an invalid WAV format")
            pcm = bytearray(audio.readframes(audio.getnframes()))
    except (OSError, EOFError, wave.Error) as error:
        raise DiarizationRuntimeError("Remote-speaker timeline could not be read") from error
    return {"waveform": tensor_factory(pcm), "sample_rate": TARGET_SAMPLE_RATE}


class PyannoteDiarizationEngine:
    engine_name = "pyannote.audio"
    model_name = "speaker-diarization-community-1"

    def __init__(
        self,
        model_directory: Path,
        *,
        pipeline_loader: Callable[[Path], _Pipeline] = _default_pipeline_loader,
        device_factory: Callable[[], object] = _default_device,
        waveform_loader: Callable[[Path], object] = _load_waveform_input,
    ):
        self.model_directory = model_directory
        self.pipeline_loader = pipeline_loader
        self.device_factory = device_factory
        self.waveform_loader = waveform_loader
        self._pipeline: _Pipeline | None = None

    def diarize(
        self,
        audio_path: Path,
        *,
        session_id: str,
        run_id: str,
        min_speakers: int | None,
        max_speakers: int | None,
        cancel_requested: Callable[[], bool],
    ) -> DiarizationDocument:
        if min_speakers is not None and min_speakers < 1:
            raise ValueError("Minimum remote speaker count must be positive")
        if max_speakers is not None and max_speakers < 1:
            raise ValueError("Maximum remote speaker count must be positive")
        if min_speakers is not None and max_speakers is not None and min_speakers > max_speakers:
            raise ValueError("Minimum remote speaker count cannot exceed the maximum")
        if cancel_requested():
            raise DiarizationCancelled("Remote-speaker separation was cancelled")
        pipeline = self._load_pipeline()
        try:
            output = cast(
                _PipelineOutput,
                pipeline(
                    self.waveform_loader(audio_path),
                    min_speakers=min_speakers,
                    max_speakers=max_speakers,
                ),
            )
        except Exception as error:
            raise DiarizationRuntimeError("Local remote-speaker separation failed") from error
        if cancel_requested():
            raise DiarizationCancelled("Remote-speaker separation was cancelled")
        turns = _normalized_turns(output.exclusive_speaker_diarization)
        return DiarizationDocument.new(
            session_id,
            run_id,
            engine=self.engine_name,
            model=self.model_name,
            turns=turns,
        )

    def _load_pipeline(self) -> _Pipeline:
        if self._pipeline is None:
            pipeline = self.pipeline_loader(self.model_directory)
            pipeline.to(self.device_factory())
            self._pipeline = pipeline
        return self._pipeline


def _normalized_turns(annotation: _Annotation) -> tuple[DiarizationTurn, ...]:
    raw_turns = sorted(
        (
            (round(segment.start * 1_000), round(segment.end * 1_000), label)
            for segment, _track, label in annotation.itertracks(yield_label=True)
        ),
        key=lambda item: (item[0], item[1], item[2]),
    )
    labels: dict[str, str] = {}
    normalized: list[DiarizationTurn] = []
    for raw_start, raw_end, raw_label in raw_turns:
        label = labels.setdefault(raw_label, f"remote-{len(labels) + 1}")
        start_ms = max(raw_start, normalized[-1].end_ms if normalized else 0)
        if raw_end <= start_ms:
            continue
        if normalized and normalized[-1].speaker_id == label and start_ms <= normalized[-1].end_ms:
            previous = normalized.pop()
            normalized.append(DiarizationTurn(previous.start_ms, raw_end, label))
        else:
            normalized.append(DiarizationTurn(start_ms, raw_end, label))
    return tuple(normalized)

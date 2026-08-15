from __future__ import annotations

import importlib
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast


class ModelDownloadError(RuntimeError):
    """Raised when a transcription model cannot be acquired safely."""


class ModelDownloadDependencyUnavailable(ModelDownloadError):
    """Raised when the optional Hugging Face download runtime is unavailable."""


class ModelDownloadCancelled(ModelDownloadError):
    """Raised when a model download is cooperatively cancelled."""


@dataclass(frozen=True, slots=True)
class ModelDownloadResult:
    repo_id: str
    revision: str
    cache_path: Path
    total_bytes: int


MODEL_REPOSITORIES: Mapping[str, str] = {
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
}

MODEL_ALLOW_PATTERNS = (
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
)


class SnapshotDownload(Protocol):
    def __call__(
        self,
        repo_id: str,
        *,
        revision: str | None = None,
        cache_dir: str,
        allow_patterns: tuple[str, ...],
        max_workers: int = 1,
        tqdm_class: type[object] | None = None,
        dry_run: bool = False,
    ) -> object: ...


class _ProgressBar(Protocol):
    desc: str | None
    unit: str
    n: float

    def update(self, amount: float = 1.0) -> object: ...


def _load_snapshot_download() -> SnapshotDownload:
    # Hugging Face enables its optional Xet transport automatically when hf_xet is
    # installed. On Windows desktop builds that transport can preallocate the full
    # model while reporting no useful progress, and interrupted transfers can leave
    # a second unusable partial file. The regular HTTPS downloader is resumable,
    # observable through tqdm, and considerably easier for users to recover.
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    try:
        module = importlib.import_module("huggingface_hub")
        constants = importlib.import_module("huggingface_hub.constants")
        # The constants module may already have been imported by faster-whisper.
        # Keep its in-process value aligned with the environment override.
        constants.__dict__["HF_HUB_DISABLE_XET"] = True
        return cast(SnapshotDownload, module.snapshot_download)
    except (ImportError, AttributeError) as error:
        raise ModelDownloadDependencyUnavailable(
            "Install the transcription extra before downloading a speech model"
        ) from error


def _progress_tqdm_class(
    *,
    cached_bytes: int,
    total_bytes: int,
    cancel_requested: Callable[[], bool],
    progress_callback: Callable[[int, int], None],
) -> type[object]:
    try:
        module = importlib.import_module("tqdm.auto")
        base = cast(type[_ProgressBar], module.tqdm)
        base_update = cast(Callable[[object, float], object], base.update)
    except (ImportError, AttributeError) as error:
        raise ModelDownloadDependencyUnavailable(
            "Install the transcription extra before downloading a speech model"
        ) from error

    report_step = max(1_048_576, total_bytes // 100)
    last_reported = cached_bytes

    def update(progress: object, amount: float = 1.0) -> object:
        nonlocal last_reported
        result = base_update(progress, amount)
        if cancel_requested():
            raise ModelDownloadCancelled("Speech model download was cancelled")
        description = str(getattr(progress, "desc", ""))
        if getattr(progress, "unit", None) == "B" and description.startswith("Downloading bytes"):
            transferred = max(0, int(cast(float, getattr(progress, "n", 0))))
            available = min(total_bytes, cached_bytes + transferred)
            if available == total_bytes or available - last_reported >= report_step:
                progress_callback(available, total_bytes)
                last_reported = available
        return result

    return cast(type[object], type("ModelDownloadProgress", (base,), {"update": update}))


class TranscriptionModelManager:
    """Acquire a known faster-whisper snapshot with persisted, cancellable progress."""

    def __init__(
        self,
        cache_path: Path,
        *,
        snapshot_download: SnapshotDownload | None = None,
    ):
        self.cache_path = cache_path
        self.snapshot_download = snapshot_download

    def ensure_available(
        self,
        model_name: str,
        *,
        cancel_requested: Callable[[], bool],
        progress_callback: Callable[[int, int], None],
    ) -> ModelDownloadResult:
        repo_id = MODEL_REPOSITORIES.get(model_name)
        if repo_id is None:
            raise ModelDownloadError(f"Unsupported transcription model {model_name}")
        if cancel_requested():
            raise ModelDownloadCancelled("Speech model download was cancelled")

        self.cache_path.mkdir(parents=True, exist_ok=True)
        downloader = self.snapshot_download or _load_snapshot_download()
        last_progress: tuple[int, int] | None = None

        def report_progress(downloaded_bytes: int, total_bytes: int) -> None:
            nonlocal last_progress
            progress = (downloaded_bytes, total_bytes)
            if progress != last_progress:
                progress_callback(downloaded_bytes, total_bytes)
                last_progress = progress

        try:
            dry_run = downloader(
                repo_id,
                cache_dir=str(self.cache_path),
                allow_patterns=MODEL_ALLOW_PATTERNS,
                max_workers=1,
                dry_run=True,
            )
            files = cast(list[object], dry_run)
            if not files:
                raise ModelDownloadError(f"Speech model {model_name} has no downloadable files")
            total_bytes = sum(self._file_size(file) for file in files)
            cached_bytes = sum(
                self._file_size(file)
                for file in files
                if not bool(getattr(file, "will_download", True))
            )
            revisions = {str(getattr(file, "commit_hash", "")).strip() for file in files}
            revisions.discard("")
            if total_bytes <= 0 or len(revisions) != 1:
                raise ModelDownloadError(
                    f"Speech model {model_name} returned incomplete download metadata"
                )
            revision = revisions.pop()
            report_progress(cached_bytes, total_bytes)
            if cancel_requested():
                raise ModelDownloadCancelled("Speech model download was cancelled")

            if cached_bytes < total_bytes:
                progress_type = _progress_tqdm_class(
                    cached_bytes=cached_bytes,
                    total_bytes=total_bytes,
                    cancel_requested=cancel_requested,
                    progress_callback=report_progress,
                )
                downloader(
                    repo_id,
                    revision=revision,
                    cache_dir=str(self.cache_path),
                    allow_patterns=MODEL_ALLOW_PATTERNS,
                    max_workers=1,
                    tqdm_class=progress_type,
                )
            if cancel_requested():
                raise ModelDownloadCancelled("Speech model download was cancelled")
            report_progress(total_bytes, total_bytes)
        except (ModelDownloadCancelled, ModelDownloadDependencyUnavailable, ModelDownloadError):
            raise
        except Exception as error:
            detail = _safe_error_detail(error)
            raise ModelDownloadError(
                f"Could not download speech model {model_name}. {detail} "
                "Check the internet connection and retry; an interrupted download does not "
                "remove recordings."
            ) from error

        return ModelDownloadResult(
            repo_id=repo_id,
            revision=revision,
            cache_path=self.cache_path,
            total_bytes=total_bytes,
        )

    @staticmethod
    def _file_size(file: object) -> int:
        value = getattr(file, "file_size", None)
        if not isinstance(value, int) or value < 0:
            raise ModelDownloadError("Speech model returned an invalid file size")
        return value


def _safe_error_detail(error: Exception) -> str:
    """Return useful diagnostics without exposing signed URL query parameters."""
    detail = " ".join(str(error).split())
    detail = re.sub(r"(https?://[^?\s]+)\?[^\s]+", r"\1?[redacted]", detail)
    if len(detail) > 300:
        detail = f"{detail[:297]}..."
    error_type = type(error).__name__
    return f"{error_type}: {detail}" if detail else error_type

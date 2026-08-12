from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from meeting_transcriber.processing.model_download import (
    MODEL_ALLOW_PATTERNS,
    ModelDownloadCancelled,
    TranscriptionModelManager,
)


@dataclass(frozen=True)
class FakeDryRunFile:
    filename: str
    file_size: int
    commit_hash: str
    will_download: bool


class FakeSnapshotDownload:
    def __init__(self, *, cancel_during_download: bool = False):
        self.cancel_during_download = cancel_during_download
        self.cancelled = False
        self.calls: list[dict[str, object]] = []

    def __call__(self, repo_id: str, **options: object) -> object:
        self.calls.append({"repo_id": repo_id, **options})
        if options.get("dry_run") is True:
            return [
                FakeDryRunFile("config.json", 100, "fixture-revision", False),
                FakeDryRunFile("model.bin", 900, "fixture-revision", True),
            ]
        progress_type = options["tqdm_class"]
        assert isinstance(progress_type, type)
        progress = progress_type(total=0, desc="Downloading bytes", unit="B")
        progress.update(450)
        if self.cancel_during_download:
            self.cancelled = True
        progress.update(450)
        progress.close()
        return str(Path(str(options["cache_dir"])) / "snapshot")


def test_manager_pins_snapshot_and_reports_cached_plus_downloaded_bytes(tmp_path: Path) -> None:
    downloader = FakeSnapshotDownload()
    progress: list[tuple[int, int]] = []
    manager = TranscriptionModelManager(tmp_path / "models", snapshot_download=downloader)

    result = manager.ensure_available(
        "small",
        cancel_requested=lambda: downloader.cancelled,
        progress_callback=lambda downloaded, total: progress.append((downloaded, total)),
    )

    assert result.repo_id == "Systran/faster-whisper-small"
    assert result.revision == "fixture-revision"
    assert result.total_bytes == 1_000
    assert progress == [(100, 1_000), (1_000, 1_000)]
    assert downloader.calls[0]["dry_run"] is True
    assert downloader.calls[1]["revision"] == "fixture-revision"
    assert downloader.calls[1]["allow_patterns"] == MODEL_ALLOW_PATTERNS
    assert downloader.calls[1]["max_workers"] == 1


def test_manager_cancels_inside_download_progress_update(tmp_path: Path) -> None:
    downloader = FakeSnapshotDownload(cancel_during_download=True)
    manager = TranscriptionModelManager(tmp_path / "models", snapshot_download=downloader)

    with pytest.raises(ModelDownloadCancelled, match="cancelled"):
        manager.ensure_available(
            "medium",
            cancel_requested=lambda: downloader.cancelled,
            progress_callback=lambda _downloaded, _total: None,
        )

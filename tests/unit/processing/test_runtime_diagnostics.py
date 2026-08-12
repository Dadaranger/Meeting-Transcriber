from __future__ import annotations

import json
from pathlib import Path

from meeting_transcriber.processing.diarization_engine import (
    PYANNOTE_MODEL_DIRECTORY,
    PYANNOTE_MODEL_MARKER,
    PYANNOTE_REPOSITORY,
    PYANNOTE_REVISION,
)
from meeting_transcriber.processing.runtime_diagnostics import inspect_diarization_runtime


def test_diarization_diagnostics_report_a_complete_pinned_model(
    tmp_path: Path,
) -> None:
    model = tmp_path / PYANNOTE_MODEL_DIRECTORY
    model.mkdir()
    (model / "config.yaml").write_text("pipeline: fixture", encoding="utf-8")
    (model / PYANNOTE_MODEL_MARKER).write_text(
        json.dumps({"repository": PYANNOTE_REPOSITORY, "revision": PYANNOTE_REVISION}),
        encoding="utf-8",
    )

    status = inspect_diarization_runtime(tmp_path)

    assert status.model_cached
    assert status.model_directory == model
    assert "Community-1 model: cached" in status.summary

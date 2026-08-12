from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path
from typing import cast

from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.domain.session import SessionState
from meeting_transcriber.storage.session_store import SessionStore


def test_fresh_process_recovers_finalized_chunks_after_forced_termination(
    tmp_path: Path,
) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "forced_exit_capture.py"

    child = subprocess.run(
        [sys.executable, str(fixture), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert child.returncode == 23, child.stderr
    fresh_service = MeetingSessionService(SessionStore(tmp_path))
    stale = fresh_service.recent_sessions()
    assert len(stale) == 1
    assert stale[0].state is SessionState.RECORDING

    interrupted = fresh_service.recover_abandoned_recordings()

    assert len(interrupted) == 1
    assert interrupted[0].state is SessionState.INTERRUPTED
    assert fresh_service.has_recoverable_audio(interrupted[0].session_id)
    session_directory = fresh_service.session_directory(interrupted[0].session_id)
    document = cast(
        dict[str, object],
        json.loads((session_directory / "capture.json").read_text(encoding="utf-8")),
    )
    assert document["state"] == "recording"
    wav_paths = sorted((session_directory / "audio").glob("*.wav"))
    assert len(wav_paths) >= 2
    with wave.open(str(wav_paths[0]), "rb") as wav_file:
        assert wav_file.getnframes() > 0

    recovered = fresh_service.recover_interrupted_session(interrupted[0].session_id)

    assert recovered.state is SessionState.RECORDED

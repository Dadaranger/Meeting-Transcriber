from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.capture.devices import AudioDevice, AudioDeviceKind
from meeting_transcriber.capture.formats import AudioFormat
from meeting_transcriber.capture.recorder import DualSourceCapture
from meeting_transcriber.capture.streams import AudioInputStream, SourceCaptureConfig
from meeting_transcriber.domain.session import SessionState
from meeting_transcriber.storage.session_store import SessionStore


class SyntheticStream:
    def __init__(self, pcm: bytes):
        self.pcm = pcm

    def start(self) -> None:
        pass

    def read(self, frame_count: int) -> bytes:
        if frame_count != 2:
            raise ValueError("Synthetic forced-exit stream expects two frames")
        time.sleep(0.002)
        return self.pcm

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


class SyntheticStreamFactory:
    def open_input(self, config: SourceCaptureConfig) -> AudioInputStream:
        sample = b"\x01\x00" if config.device.kind is AudioDeviceKind.MICROPHONE else b"\x02\x00"
        return SyntheticStream(sample * 2)


def _config(kind: AudioDeviceKind, backend_index: int) -> SourceCaptureConfig:
    device = AudioDevice(
        device_id=f"synthetic-{kind.value}",
        backend_index=backend_index,
        name=f"Synthetic {kind.value}",
        kind=kind,
        host_api="Synthetic",
        max_input_channels=1,
        default_sample_rate=8,
    )
    return SourceCaptureConfig(device, AudioFormat(sample_rate=8, channels=1), frames_per_buffer=2)


def _both_sources_have_finalized_chunks(manifest_path: Path) -> bool:
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    sources = document.get("sources") if isinstance(document, dict) else None
    return (
        isinstance(sources, list)
        and len(sources) == 2
        and all(isinstance(source, dict) and source.get("chunks") for source in sources)
    )


def main(meeting_root: Path) -> None:
    sessions = MeetingSessionService(SessionStore(meeting_root))
    draft = sessions.create_draft("Forced termination probe")
    sessions.confirm_recording_consent(draft.session_id)
    sessions.transition_state(draft.session_id, SessionState.RECORDING)
    session_directory = sessions.session_directory(draft.session_id)
    capture = DualSourceCapture(
        draft.session_id,
        session_directory,
        (
            _config(AudioDeviceKind.MICROPHONE, 1),
            _config(AudioDeviceKind.SYSTEM_LOOPBACK, 2),
        ),
        SyntheticStreamFactory(),
        chunk_duration_seconds=0.25,
    )
    capture.start()

    deadline = time.monotonic() + 5.0
    manifest_path = session_directory / "capture.json"
    while time.monotonic() < deadline:
        if _both_sources_have_finalized_chunks(manifest_path):
            os._exit(23)
        time.sleep(0.01)
    raise RuntimeError("Synthetic capture did not finalize chunks before the forced exit")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: forced_exit_capture.py MEETING_ROOT")
    main(Path(sys.argv[1]))

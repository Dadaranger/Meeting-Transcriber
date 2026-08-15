import json
import wave
from pathlib import Path

import pytest

from meeting_transcriber.app.session_service import (
    MeetingSessionService,
    SessionRecoveryError,
)
from meeting_transcriber.domain.session import (
    CONSENT_STATEMENT_VERSION,
    REQUIRED_CONSENT_SOURCES,
    SessionState,
)
from meeting_transcriber.storage.session_store import SessionStore


def test_service_persists_current_recording_consent(tmp_path: Path) -> None:
    service = MeetingSessionService(SessionStore(tmp_path))
    draft = service.create_draft("Weekly sync")

    confirmed = service.confirm_recording_consent(draft.session_id)

    assert confirmed.has_current_recording_consent
    assert confirmed.consent_text_version == CONSENT_STATEMENT_VERSION
    assert confirmed.consent_capture_sources == REQUIRED_CONSENT_SOURCES
    assert service.get_session(draft.session_id) == confirmed


@pytest.mark.parametrize("abandoned_state", [SessionState.RECORDING, SessionState.PAUSED])
def test_startup_marks_abandoned_capture_interrupted(
    tmp_path: Path,
    abandoned_state: SessionState,
) -> None:
    service = MeetingSessionService(SessionStore(tmp_path))
    draft = service.create_draft("Abandoned meeting")
    service.confirm_recording_consent(draft.session_id)
    service.transition_state(draft.session_id, SessionState.RECORDING)
    if abandoned_state is SessionState.PAUSED:
        service.transition_state(draft.session_id, SessionState.PAUSED)

    interrupted = service.recover_abandoned_recordings()

    assert len(interrupted) == 1
    assert interrupted[0].state is SessionState.INTERRUPTED
    assert service.get_session(draft.session_id).state is SessionState.INTERRUPTED


def test_interrupted_session_recovers_only_with_manifest_and_wav(tmp_path: Path) -> None:
    service = MeetingSessionService(SessionStore(tmp_path))
    draft = service.create_draft("Recoverable meeting")
    service.confirm_recording_consent(draft.session_id)
    service.transition_state(draft.session_id, SessionState.RECORDING)
    service.transition_state(draft.session_id, SessionState.INTERRUPTED)
    directory = service.session_directory(draft.session_id)
    (directory / "capture.json").write_text(
        json.dumps({"session_id": draft.session_id}),
        encoding="utf-8",
    )
    audio_directory = directory / "audio"
    audio_directory.mkdir()
    with wave.open(str(audio_directory / "microphone_0001.wav"), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(48_000)
        wav_file.writeframes(b"\x00\x00" * 8)

    recovered = service.recover_interrupted_session(draft.session_id)

    assert recovered.state is SessionState.RECORDED
    assert service.get_session(draft.session_id) == recovered


def test_interrupted_session_without_audio_stays_interrupted(tmp_path: Path) -> None:
    service = MeetingSessionService(SessionStore(tmp_path))
    draft = service.create_draft("Missing audio")
    service.confirm_recording_consent(draft.session_id)
    service.transition_state(draft.session_id, SessionState.RECORDING)
    service.transition_state(draft.session_id, SessionState.INTERRUPTED)

    with pytest.raises(SessionRecoveryError, match="No finalized audio"):
        service.recover_interrupted_session(draft.session_id)

    assert service.get_session(draft.session_id).state is SessionState.INTERRUPTED


def test_corrupt_wav_is_not_recoverable(tmp_path: Path) -> None:
    service = MeetingSessionService(SessionStore(tmp_path))
    draft = service.create_draft("Corrupt audio")
    directory = service.session_directory(draft.session_id)
    (directory / "capture.json").write_text(
        json.dumps({"session_id": draft.session_id}),
        encoding="utf-8",
    )
    audio_directory = directory / "audio"
    audio_directory.mkdir()
    (audio_directory / "microphone_0001.wav").write_bytes(b"not-a-wave")

    assert not service.has_recoverable_audio(draft.session_id)

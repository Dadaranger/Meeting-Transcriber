from __future__ import annotations

import wave
from pathlib import Path

import pytest

from meeting_transcriber.app.media_import_service import MeetingMediaImportService
from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.domain.session import SessionOrigin, SessionState
from meeting_transcriber.domain.transcript import TranscriptSource
from meeting_transcriber.processing.imported_media import (
    IMPORTED_MEDIA_MANIFEST_NAME,
    ImportedMediaError,
    ImportedMediaManifestStore,
    PyAVAudioExtractor,
)
from meeting_transcriber.processing.preparation import AudioPreparationService
from meeting_transcriber.storage.session_store import SessionStore

RUN_ID = "2e28f152-522a-4399-8313-d6d645703991"


class FakeExtractor:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    def extract(self, source: Path, destination: Path) -> int:
        self.calls.append((source, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16_000)
            wav_file.writeframes(b"\x01\x00" * 32_000)
        return 32_000


def test_import_registers_authorized_video_as_readable_session(tmp_path: Path) -> None:
    source = tmp_path / "Team planning 2026.mp4"
    source.write_bytes(b"synthetic-video-container")
    meeting_root = tmp_path / "meetings"
    sessions = MeetingSessionService(SessionStore(meeting_root))

    session = MeetingMediaImportService(sessions).import_file(
        source,
        title=" Team planning ",
        authorization_confirmed=True,
    )
    manifest = ImportedMediaManifestStore(
        sessions.session_directory(session.session_id) / IMPORTED_MEDIA_MANIFEST_NAME
    ).load()

    assert session.title == "Team planning"
    assert session.origin is SessionOrigin.IMPORTED_MEDIA
    assert session.state is SessionState.RECORDED
    assert session.started_at == session.created_at
    assert session.stopped_at == session.created_at
    assert manifest.session_id == session.session_id
    assert manifest.source_path == source.resolve()
    assert manifest.source_name == source.name
    assert manifest.media_kind.value == "video"
    assert manifest.authorization_confirmed_at.tzinfo is not None


def test_import_rejects_missing_authorization_and_unknown_files(tmp_path: Path) -> None:
    source = tmp_path / "recording.txt"
    source.write_text("not media", encoding="utf-8")
    service = MeetingMediaImportService(MeetingSessionService(SessionStore(tmp_path / "meetings")))

    with pytest.raises(ImportedMediaError, match="authorized"):
        service.import_file(source, authorization_confirmed=False)
    with pytest.raises(ImportedMediaError, match="Unsupported media type"):
        service.import_file(source, authorization_confirmed=True)


def test_preparation_decodes_import_once_and_reuses_normalized_audio(tmp_path: Path) -> None:
    source = tmp_path / "interview.m4a"
    source.write_bytes(b"synthetic-audio-container")
    sessions = MeetingSessionService(SessionStore(tmp_path / "meetings"))
    session = MeetingMediaImportService(sessions).import_file(
        source,
        authorization_confirmed=True,
    )
    extractor = FakeExtractor()
    service = AudioPreparationService(extractor)
    session_directory = sessions.session_directory(session.session_id)

    first = service.prepare(session_directory, RUN_ID)
    second = service.prepare(session_directory, RUN_ID)

    assert first == second
    assert first.session_id == session.session_id
    assert first.total_audio_ms == 2_000
    assert first.timeline_duration_ms == 2_000
    assert first.chunks[0].source is TranscriptSource.IMPORTED_MEDIA
    assert first.chunks[0].path.name == "imported-media.wav"
    assert extractor.calls == [(source.resolve(), first.chunks[0].path)]


def test_pyav_extracts_wav_as_16khz_mono_without_changing_source(tmp_path: Path) -> None:
    source = tmp_path / "stereo.wav"
    with wave.open(str(source), "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(48_000)
        wav_file.writeframes(b"\x10\x00\xf0\xff" * 4_800)
    original = source.read_bytes()
    destination = tmp_path / "decoded.wav"

    frames = PyAVAudioExtractor().extract(source, destination)

    assert frames == 1_600
    assert source.read_bytes() == original
    with wave.open(str(destination), "rb") as wav_file:
        assert wav_file.getframerate() == 16_000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnframes() == 1_600


def test_import_manifest_rejects_invalid_timestamp(tmp_path: Path) -> None:
    path = tmp_path / IMPORTED_MEDIA_MANIFEST_NAME
    path.write_text(
        '{"schema_version": 1, "session_id": "2e28f152-522a-4399-8313-d6d645703991", '
        '"source_path": "/tmp/recording.mp3", "source_name": "recording.mp3", '
        '"media_kind": "audio", "source_size_bytes": 1, "imported_at": "invalid", '
        '"authorization_confirmed_at": "2026-08-20T00:00:00Z"}',
        encoding="utf-8",
    )

    with pytest.raises(ImportedMediaError, match="imported_at"):
        ImportedMediaManifestStore(path).load()

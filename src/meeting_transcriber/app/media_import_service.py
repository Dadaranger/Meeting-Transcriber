from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.domain.session import MeetingSession
from meeting_transcriber.processing.imported_media import (
    IMPORTED_MEDIA_MANIFEST_NAME,
    ImportedMediaError,
    ImportedMediaManifest,
    ImportedMediaManifestStore,
    media_kind_for,
)


class MediaImportWorkflow(Protocol):
    def import_file(
        self,
        source: Path,
        *,
        title: str | None = None,
        authorization_confirmed: bool,
    ) -> MeetingSession: ...


class MeetingMediaImportService:
    """Register user-selected media as a durable local transcription session."""

    def __init__(self, session_service: MeetingSessionService):
        self.session_service = session_service

    def import_file(
        self,
        source: Path,
        *,
        title: str | None = None,
        authorization_confirmed: bool,
    ) -> MeetingSession:
        if not authorization_confirmed:
            raise ImportedMediaError(
                "Confirm that you are authorized to process the selected recording"
            )
        try:
            resolved = source.expanduser().resolve(strict=True)
            size = resolved.stat().st_size
        except OSError as error:
            raise ImportedMediaError(
                f"The selected media file could not be opened: {source}"
            ) from error
        if not resolved.is_file():
            raise ImportedMediaError("Choose an audio or video file, not a folder")
        if size <= 0:
            raise ImportedMediaError("The selected media file is empty")
        kind = media_kind_for(resolved)
        session_title = (title or resolved.stem).strip() or "Imported recording"
        imported_at = datetime.now(UTC)
        session = self.session_service.create_imported(session_title)
        manifest = ImportedMediaManifest(
            session_id=session.session_id,
            source_path=resolved,
            source_name=resolved.name,
            media_kind=kind,
            source_size_bytes=size,
            imported_at=imported_at,
            authorization_confirmed_at=imported_at,
        )
        session_directory = self.session_service.session_directory(session.session_id)
        ImportedMediaManifestStore(session_directory / IMPORTED_MEDIA_MANIFEST_NAME).save(manifest)
        return session

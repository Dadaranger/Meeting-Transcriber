from __future__ import annotations

import json
import os
import tempfile
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Protocol, cast
from uuid import UUID

AUDIO_EXTENSIONS = frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma"})
VIDEO_EXTENSIONS = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm", ".wmv"})
SUPPORTED_MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
MEDIA_FILE_FILTER = (
    "Audio and video files "
    "(*.aac *.avi *.flac *.m4a *.m4v *.mkv *.mov *.mp3 *.mp4 *.ogg *.opus "
    "*.wav *.webm *.wma *.wmv);;All files (*)"
)
IMPORTED_MEDIA_MANIFEST_NAME = "import.json"
IMPORTED_AUDIO_NAME = "imported-media.wav"
TARGET_SAMPLE_RATE = 16_000
TARGET_CHANNELS = 1
TARGET_SAMPLE_WIDTH_BYTES = 2


class MediaKind(StrEnum):
    AUDIO = "audio"
    VIDEO = "video"


class ImportedMediaError(RuntimeError):
    """Raised when selected media cannot be registered or decoded locally."""


class ImportedMediaDecodeError(ImportedMediaError):
    """Raised when a supported container does not yield usable audio."""


def media_kind_for(path: Path) -> MediaKind:
    suffix = path.suffix.lower()
    if suffix in AUDIO_EXTENSIONS:
        return MediaKind.AUDIO
    if suffix in VIDEO_EXTENSIONS:
        return MediaKind.VIDEO
    supported = ", ".join(sorted(SUPPORTED_MEDIA_EXTENSIONS))
    raise ImportedMediaError(f"Unsupported media type {suffix or '(none)'}. Choose: {supported}")


def _utc_timestamp(value: datetime | None = None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Imported-media timestamps must be timezone-aware")
    return timestamp.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ImportedMediaError(f"{field} must be an ISO-8601 timestamp")
    try:
        return _utc_timestamp(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as error:
        raise ImportedMediaError(f"{field} must be an ISO-8601 timestamp") from error


@dataclass(frozen=True, slots=True)
class ImportedMediaManifest:
    SCHEMA_VERSION: ClassVar[int] = 1

    session_id: str
    source_path: Path
    source_name: str
    media_kind: MediaKind
    source_size_bytes: int
    imported_at: datetime
    authorization_confirmed_at: datetime

    def __post_init__(self) -> None:
        UUID(self.session_id)
        if not self.source_path.is_absolute():
            raise ValueError("Imported media source path must be absolute")
        if not self.source_name or Path(self.source_name).name != self.source_name:
            raise ValueError("Imported media source name must be a filename")
        if self.source_size_bytes <= 0:
            raise ValueError("Imported media source must not be empty")
        _utc_timestamp(self.imported_at)
        _utc_timestamp(self.authorization_confirmed_at)


class ImportedMediaManifestStore:
    def __init__(self, path: Path):
        self.path = path

    def save(self, manifest: ImportedMediaManifest) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": manifest.SCHEMA_VERSION,
            "session_id": manifest.session_id,
            "source_path": str(manifest.source_path),
            "source_name": manifest.source_name,
            "media_kind": manifest.media_kind.value,
            "source_size_bytes": manifest.source_size_bytes,
            "imported_at": _format_timestamp(manifest.imported_at),
            "authorization_confirmed_at": _format_timestamp(manifest.authorization_confirmed_at),
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="import-",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return self.path

    def load(self) -> ImportedMediaManifest:
        try:
            raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ImportedMediaError("Imported-media manifest could not be read") from error
        if not isinstance(raw, dict):
            raise ImportedMediaError("Imported-media manifest must be a JSON object")
        document = cast(dict[str, object], raw)
        if document.get("schema_version") != ImportedMediaManifest.SCHEMA_VERSION:
            raise ImportedMediaError("Imported-media manifest uses an unsupported schema")

        def required_string(field: str) -> str:
            value = document.get(field)
            if not isinstance(value, str) or not value:
                raise ImportedMediaError(f"{field} must be a non-empty string")
            return value

        size = document.get("source_size_bytes")
        if isinstance(size, bool) or not isinstance(size, int):
            raise ImportedMediaError("source_size_bytes must be an integer")
        try:
            return ImportedMediaManifest(
                session_id=required_string("session_id"),
                source_path=Path(required_string("source_path")),
                source_name=required_string("source_name"),
                media_kind=MediaKind(required_string("media_kind")),
                source_size_bytes=size,
                imported_at=_parse_timestamp(document.get("imported_at"), "imported_at"),
                authorization_confirmed_at=_parse_timestamp(
                    document.get("authorization_confirmed_at"),
                    "authorization_confirmed_at",
                ),
            )
        except (TypeError, ValueError) as error:
            raise ImportedMediaError("Imported-media manifest contains invalid values") from error


class MediaAudioExtractor(Protocol):
    def extract(self, source: Path, destination: Path) -> int: ...


class PyAVAudioExtractor:
    """Decode the first audio stream to immutable 16 kHz mono PCM for transcription."""

    def extract(self, source: Path, destination: Path) -> int:
        try:
            import av
        except ImportError as error:
            raise ImportedMediaDecodeError(
                "The local media decoder is unavailable; reinstall Meeting Transcriber"
            ) from error

        if not source.is_file():
            raise ImportedMediaDecodeError(
                f"The imported source file is no longer available: {source}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.unlink(missing_ok=True)
        frame_count = 0
        try:
            with av.open(str(source)) as container:
                audio_stream = next(
                    (stream for stream in container.streams if stream.type == "audio"),
                    None,
                )
                if audio_stream is None:
                    raise ImportedMediaDecodeError(f"{source.name} does not contain an audio track")
                resampler = av.AudioResampler(
                    format="s16",
                    layout="mono",
                    rate=TARGET_SAMPLE_RATE,
                )
                with wave.open(str(temporary), "wb") as wav_file:
                    wav_file.setnchannels(TARGET_CHANNELS)
                    wav_file.setsampwidth(TARGET_SAMPLE_WIDTH_BYTES)
                    wav_file.setframerate(TARGET_SAMPLE_RATE)
                    for frame in container.decode(audio_stream):
                        for normalized in resampler.resample(cast(av.AudioFrame, frame)):
                            payload_size = normalized.samples * TARGET_SAMPLE_WIDTH_BYTES
                            wav_file.writeframesraw(bytes(normalized.planes[0])[:payload_size])
                            frame_count += normalized.samples
                    for normalized in resampler.resample(None):
                        payload_size = normalized.samples * TARGET_SAMPLE_WIDTH_BYTES
                        wav_file.writeframesraw(bytes(normalized.planes[0])[:payload_size])
                        frame_count += normalized.samples
            if frame_count <= 0:
                raise ImportedMediaDecodeError(
                    f"No readable audio samples were found in {source.name}"
                )
            os.replace(temporary, destination)
        except ImportedMediaDecodeError:
            raise
        except Exception as error:
            raise ImportedMediaDecodeError(
                f"Could not decode audio from {source.name}: {error}"
            ) from error
        finally:
            temporary.unlink(missing_ok=True)
        return frame_count

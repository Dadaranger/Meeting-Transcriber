from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path
from threading import RLock

from meeting_transcriber.capture.chunks import AudioChunkMetadata
from meeting_transcriber.capture.devices import AudioDeviceKind
from meeting_transcriber.capture.formats import AudioFormat
from meeting_transcriber.capture.streams import SourceCaptureConfig


class CaptureJournalState(StrEnum):
    RECORDING = "recording"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class CaptureSourceManifest:
    source: AudioDeviceKind
    device_id: str
    device_name: str
    audio_format: AudioFormat
    started_monotonic_ns: int
    stopped_monotonic_ns: int | None = None
    chunks: tuple[AudioChunkMetadata, ...] = ()

    @property
    def frame_count(self) -> int:
        return sum(chunk.frame_count for chunk in self.chunks)


@dataclass(frozen=True, slots=True)
class CaptureManifest:
    schema_version: int
    session_id: str
    state: CaptureJournalState
    started_monotonic_ns: int
    updated_monotonic_ns: int
    stopped_monotonic_ns: int | None
    sources: tuple[CaptureSourceManifest, ...]
    errors: tuple[str, ...] = ()


class CaptureManifestStore:
    def __init__(self, path: Path):
        self.path = path

    def save(self, manifest: CaptureManifest) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": manifest.schema_version,
            "session_id": manifest.session_id,
            "state": manifest.state.value,
            "started_monotonic_ns": manifest.started_monotonic_ns,
            "updated_monotonic_ns": manifest.updated_monotonic_ns,
            "stopped_monotonic_ns": manifest.stopped_monotonic_ns,
            "errors": list(manifest.errors),
            "sources": [
                {
                    "source": source.source.value,
                    "device_id": source.device_id,
                    "device_name": source.device_name,
                    "audio_format": asdict(source.audio_format),
                    "started_monotonic_ns": source.started_monotonic_ns,
                    "stopped_monotonic_ns": source.stopped_monotonic_ns,
                    "frame_count": source.frame_count,
                    "chunks": [asdict(chunk) for chunk in source.chunks],
                }
                for source in manifest.sources
            ],
        }

        descriptor, temporary_name = tempfile.mkstemp(
            prefix="capture-",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._replace_with_retry(temporary_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _replace_with_retry(self, temporary_path: Path) -> None:
        for attempt in range(5):
            try:
                os.replace(temporary_path, self.path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.01 * (attempt + 1))


class CaptureManifestJournal:
    """Thread-safe capture journal persisted whenever a WAV chunk is finalized."""

    SCHEMA_VERSION = 1

    def __init__(
        self,
        path: Path,
        session_id: str,
        configs: tuple[SourceCaptureConfig, ...],
        source_started_ns: dict[AudioDeviceKind, int],
    ):
        self._lock = RLock()
        self._store = CaptureManifestStore(path)
        started_ns = min(source_started_ns.values())
        sources = tuple(
            CaptureSourceManifest(
                source=config.device.kind,
                device_id=config.device.device_id,
                device_name=config.device.name,
                audio_format=config.audio_format,
                started_monotonic_ns=source_started_ns[config.device.kind],
            )
            for config in configs
        )
        self._manifest = CaptureManifest(
            schema_version=self.SCHEMA_VERSION,
            session_id=session_id,
            state=CaptureJournalState.RECORDING,
            started_monotonic_ns=started_ns,
            updated_monotonic_ns=started_ns,
            stopped_monotonic_ns=None,
            sources=sources,
        )
        self._store.save(self._manifest)

    def record_chunk(self, chunk: AudioChunkMetadata) -> None:
        with self._lock:
            if not any(source.source is chunk.source for source in self._manifest.sources):
                raise ValueError(f"Capture journal has no source for {chunk.source.value}")
            sources = tuple(
                replace(source, chunks=(*source.chunks, chunk))
                if source.source is chunk.source
                else source
                for source in self._manifest.sources
            )
            self._manifest = replace(
                self._manifest,
                sources=sources,
                updated_monotonic_ns=max(
                    self._manifest.updated_monotonic_ns,
                    chunk.end_monotonic_ns,
                ),
            )
            self._store.save(self._manifest)

    def finish(
        self,
        source_stopped_ns: dict[AudioDeviceKind, int],
        errors: tuple[str, ...],
    ) -> CaptureManifest:
        with self._lock:
            stopped_ns = max(source_stopped_ns.values())
            sources = tuple(
                replace(
                    source,
                    stopped_monotonic_ns=source_stopped_ns.get(source.source, stopped_ns),
                )
                for source in self._manifest.sources
            )
            self._manifest = replace(
                self._manifest,
                state=(CaptureJournalState.INTERRUPTED if errors else CaptureJournalState.STOPPED),
                updated_monotonic_ns=stopped_ns,
                stopped_monotonic_ns=stopped_ns,
                sources=sources,
                errors=errors,
            )
            self._store.save(self._manifest)
            return self._manifest

    def snapshot(self) -> CaptureManifest:
        with self._lock:
            return self._manifest

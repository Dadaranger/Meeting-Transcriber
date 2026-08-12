from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

BYTES_PER_GIBIBYTE = 1_024**3
DEFAULT_STORAGE_WARNING_BYTES = 5 * BYTES_PER_GIBIBYTE
DEFAULT_STORAGE_CRITICAL_BYTES = 1 * BYTES_PER_GIBIBYTE


class StorageHealth(StrEnum):
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class DiskUsageSnapshot:
    total: int
    used: int
    free: int


def _read_disk_usage(path: Path) -> DiskUsageSnapshot:
    usage = shutil.disk_usage(path)
    return DiskUsageSnapshot(usage.total, usage.used, usage.free)


@dataclass(frozen=True, slots=True)
class DiskSpaceStatus:
    total_bytes: int
    used_bytes: int
    free_bytes: int
    health: StorageHealth

    @property
    def free_gibibytes(self) -> float:
        return self.free_bytes / BYTES_PER_GIBIBYTE

    @property
    def display_text(self) -> str:
        available = f"{self.free_gibibytes:.1f} GiB free for local meeting audio"
        if self.health is StorageHealth.CRITICAL:
            return f"Critical storage: {available}. Recording cannot start."
        if self.health is StorageHealth.WARNING:
            return f"Low storage: {available}. Free space before a long meeting."
        return f"Storage ready: {available}."


class DiskSpaceChecker:
    def __init__(
        self,
        meeting_root: Path,
        *,
        warning_bytes: int = DEFAULT_STORAGE_WARNING_BYTES,
        critical_bytes: int = DEFAULT_STORAGE_CRITICAL_BYTES,
        disk_usage: Callable[[Path], DiskUsageSnapshot] = _read_disk_usage,
    ):
        if critical_bytes < 1:
            raise ValueError("Critical storage threshold must be positive")
        if warning_bytes <= critical_bytes:
            raise ValueError("Storage warning threshold must exceed the critical threshold")
        self.meeting_root = meeting_root
        self.warning_bytes = warning_bytes
        self.critical_bytes = critical_bytes
        self.disk_usage = disk_usage

    def check(self) -> DiskSpaceStatus:
        usage = self.disk_usage(self._existing_storage_path())
        if usage.free < self.critical_bytes:
            health = StorageHealth.CRITICAL
        elif usage.free < self.warning_bytes:
            health = StorageHealth.WARNING
        else:
            health = StorageHealth.HEALTHY
        return DiskSpaceStatus(
            total_bytes=usage.total,
            used_bytes=usage.used,
            free_bytes=usage.free,
            health=health,
        )

    def _existing_storage_path(self) -> Path:
        candidate = self.meeting_root
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        return candidate

from __future__ import annotations

from pathlib import Path

import pytest

from meeting_transcriber.app.storage_health import (
    BYTES_PER_GIBIBYTE,
    DiskSpaceChecker,
    DiskUsageSnapshot,
    StorageHealth,
)


@pytest.mark.parametrize(
    ("free_bytes", "expected"),
    [
        (10 * BYTES_PER_GIBIBYTE, StorageHealth.HEALTHY),
        (2 * BYTES_PER_GIBIBYTE, StorageHealth.WARNING),
        (BYTES_PER_GIBIBYTE // 2, StorageHealth.CRITICAL),
    ],
)
def test_disk_space_checker_classifies_available_space(
    tmp_path: Path,
    free_bytes: int,
    expected: StorageHealth,
) -> None:
    checked_paths: list[Path] = []

    def fake_disk_usage(path: Path) -> DiskUsageSnapshot:
        checked_paths.append(path)
        total = 100 * BYTES_PER_GIBIBYTE
        return DiskUsageSnapshot(total, total - free_bytes, free_bytes)

    checker = DiskSpaceChecker(tmp_path / "not-created" / "meetings", disk_usage=fake_disk_usage)

    status = checker.check()

    assert checked_paths == [tmp_path]
    assert status.health is expected
    assert status.free_bytes == free_bytes
    assert "GiB free" in status.display_text


def test_disk_space_thresholds_must_leave_a_warning_band(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exceed"):
        DiskSpaceChecker(tmp_path, warning_bytes=100, critical_bytes=100)

from __future__ import annotations

from pathlib import Path

from meeting_transcriber import __version__
from meeting_transcriber.main import main


def test_main_supports_version_and_headless_package_smoke_test() -> None:
    assert main(["--version"]) == 0
    assert main(["--smoke-test"]) == 0
    assert __version__ == "0.1.1"


def test_package_smoke_test_proves_the_entry_point_executed(tmp_path: Path) -> None:
    marker = tmp_path / "package-smoke.txt"

    assert main([f"--package-smoke-test={marker}"]) == 0

    assert marker.read_text(encoding="utf-8") == (
        f"meeting-transcriber-package-smoke:{__version__}\n"
    )

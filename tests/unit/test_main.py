from __future__ import annotations

from meeting_transcriber import __version__
from meeting_transcriber.main import main


def test_main_supports_version_and_headless_package_smoke_test() -> None:
    assert main(["--version"]) == 0
    assert main(["--smoke-test"]) == 0
    assert __version__ == "0.1.0"

from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from meeting_transcriber import __version__
from meeting_transcriber.infrastructure.paths import application_icon_path
from meeting_transcriber.ui.main_window import APP_STYLE, MainWindow


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """Create or return the process-wide Qt application."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing

    app = QApplication(list(argv) if argv is not None else sys.argv)
    app.setApplicationName("Meeting Transcriber")
    app.setApplicationDisplayName("Meeting Transcriber")
    app.setOrganizationName("Meeting Transcriber")
    app.setApplicationVersion(__version__)
    icon_path = application_icon_path()
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    return app


def main(argv: Sequence[str] | None = None) -> int:
    """Launch the desktop application."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--version"]:
        print(__version__)
        return 0
    package_smoke_marker = _package_smoke_marker(arguments)
    if arguments == ["--smoke-test"] or package_smoke_marker is not None:
        if package_smoke_marker is not None:
            importlib.import_module("faster_whisper")
            if sys.platform == "win32":
                importlib.import_module("pyaudiowpatch")
            elif sys.platform == "darwin":
                importlib.import_module("sounddevice")
                from meeting_transcriber.capture.macos_coreaudio import (
                    mac_system_audio_helper_path,
                )

                if not mac_system_audio_helper_path().is_file():
                    raise RuntimeError("The packaged macOS system-audio helper is missing")
        app = create_application(["meeting-transcriber", "-platform", "offscreen"])
        app.processEvents()
        if package_smoke_marker is not None:
            package_smoke_marker.write_text(
                f"meeting-transcriber-package-smoke:{__version__}\n",
                encoding="utf-8",
            )
        return 0
    app = create_application([sys.argv[0], *arguments])
    window = MainWindow()
    window.show()
    return app.exec()


def _package_smoke_marker(arguments: Sequence[str]) -> Path | None:
    if arguments == ["--package-smoke-test"]:
        return Path.cwd() / "meeting-transcriber-package-smoke.txt"
    if len(arguments) != 1 or not arguments[0].startswith("--package-smoke-test="):
        return None
    raw_path = arguments[0].partition("=")[2].strip()
    return Path(raw_path) if raw_path else None

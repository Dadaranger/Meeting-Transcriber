from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence

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
    if arguments in (["--smoke-test"], ["--package-smoke-test"]):
        if arguments == ["--package-smoke-test"]:
            importlib.import_module("faster_whisper")
            importlib.import_module("pyaudiowpatch")
        app = create_application(["meeting-transcriber", "-platform", "offscreen"])
        app.processEvents()
        return 0
    app = create_application([sys.argv[0], *arguments])
    window = MainWindow()
    window.show()
    return app.exec()

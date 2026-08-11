from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox
from pytestqt.qtbot import QtBot

from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.storage.session_store import SessionStore
from meeting_transcriber.ui.main_window import MainWindow


def test_main_window_exposes_home_and_diagnostics_pages(qtbot: QtBot, tmp_path: Path) -> None:
    service = MeetingSessionService(SessionStore(tmp_path))
    window = MainWindow(service)
    qtbot.addWidget(window)

    window.show()

    assert window.isVisible()
    assert window.windowTitle() == "Meeting Transcriber"
    assert window.pages.count() == 2
    assert window.pages.currentWidget() is window.home_page


def test_create_draft_button_persists_a_named_session(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MeetingSessionService(SessionStore(tmp_path))
    window = MainWindow(service)
    qtbot.addWidget(window)

    def fake_get_text(*args: object, **kwargs: object) -> tuple[str, bool]:
        return "Architecture review", True

    def fake_information(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QInputDialog, "getText", fake_get_text)
    monkeypatch.setattr(QMessageBox, "information", fake_information)

    qtbot.mouseClick(  # type: ignore[no-untyped-call]
        window.home_page.start_button,
        Qt.MouseButton.LeftButton,
    )

    sessions = service.recent_sessions()
    assert len(sessions) == 1
    assert sessions[0].title == "Architecture review"
    assert "Draft saved - Architecture review" in window.statusBar().currentMessage()

from datetime import UTC, datetime, timedelta

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from meeting_transcriber.domain.session import MeetingSession, SessionState
from meeting_transcriber.ui.history_page import HistoryPage

START = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _interrupted_session() -> MeetingSession:
    session = MeetingSession.new("Recovered interview", now=START)
    session = session.confirm_consent(at=START + timedelta(seconds=1))
    session = session.transition(SessionState.RECORDING, at=START + timedelta(seconds=2))
    return session.transition(SessionState.INTERRUPTED, at=START + timedelta(minutes=2))


def test_history_enables_recovery_only_for_interrupted_audio(qtbot: QtBot) -> None:
    page = HistoryPage()
    qtbot.addWidget(page)
    interrupted = _interrupted_session()
    draft = MeetingSession.new("Future meeting", now=START + timedelta(minutes=3))

    page.load_sessions([draft, interrupted], frozenset({interrupted.session_id}))
    page.session_list.setCurrentRow(1)

    assert page.open_folder_button.isEnabled()
    assert page.recover_button.isEnabled()
    assert "Finalized WAV chunks" in page.selection_status.text()

    with qtbot.waitSignal(page.recover_requested) as recovered:
        qtbot.mouseClick(page.recover_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]
    assert recovered.args == [interrupted.session_id]

    page.session_list.setCurrentRow(0)
    assert not page.recover_button.isEnabled()


def test_history_emits_open_folder_for_selected_session(qtbot: QtBot) -> None:
    page = HistoryPage()
    qtbot.addWidget(page)
    session = MeetingSession.new("Weekly sync", now=START)
    page.load_sessions([session], frozenset())

    with qtbot.waitSignal(page.open_folder_requested) as opened:
        qtbot.mouseClick(page.open_folder_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]

    assert opened.args == [session.session_id]

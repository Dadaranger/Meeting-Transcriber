from __future__ import annotations

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from meeting_transcriber.domain.session import MeetingSession, SessionState
from meeting_transcriber.domain.transcript import (
    TranscriptionJob,
    TranscriptionJobState,
    TranscriptionProfile,
)
from meeting_transcriber.ui.transcription_page import TranscriptionPage


def _recorded_session() -> MeetingSession:
    session = MeetingSession.new("Planning review")
    session = session.confirm_consent()
    session = session.transition(SessionState.RECORDING)
    return session.transition(SessionState.RECORDED)


def test_transcription_setup_emits_explicit_profile_language_and_download_choice(
    qtbot: QtBot,
) -> None:
    page = TranscriptionPage()
    qtbot.addWidget(page)
    session = _recorded_session()
    page.load_session(session)
    page.profile_combo.setCurrentIndex(2)
    page.language_combo.setCurrentIndex(page.language_combo.findData("en"))
    page.hotwords_input.setText("Akato, WASAPI")
    page.allow_download_checkbox.setChecked(True)

    assert "large-v3" in page.profile_description.text()
    with qtbot.waitSignal(page.start_requested) as started:
        qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]

    assert started.args == [
        session.session_id,
        TranscriptionProfile.ACCURATE.value,
        "en",
        "Akato, WASAPI",
        True,
    ]


def test_transcription_page_shows_progress_and_retryable_failure(qtbot: QtBot) -> None:
    page = TranscriptionPage()
    qtbot.addWidget(page)
    session = _recorded_session()
    job = TranscriptionJob.new(session.session_id, profile=TranscriptionProfile.FAST)
    job = job.transition(TranscriptionJobState.PREPARING)
    job = job.with_progress(500, 2_000)
    job = job.transition(TranscriptionJobState.TRANSCRIBING)

    page.load_session(session)
    page.show_job(job)

    assert page.setup_card.isHidden()
    assert not page.progress_card.isHidden()
    assert page.progress_bar.value() == 25
    assert "0.5 of 2.0" in page.progress_detail.text()

    failed = job.transition(TranscriptionJobState.FAILED, error="Model unavailable")
    page.show_job(failed)

    assert not page.setup_card.isHidden()
    assert page.progress_card.isHidden()
    assert page.start_button.text() == "Retry offline transcription"
    assert "Model unavailable" in page.previous_status.text()

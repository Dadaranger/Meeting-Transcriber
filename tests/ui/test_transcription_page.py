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
    page.separate_remote_speakers_checkbox.setChecked(True)
    page.min_remote_speakers.setValue(2)
    page.max_remote_speakers.setValue(4)
    page.allow_diarization_download_checkbox.setChecked(True)
    page.diarization_token_input.setText("temporary-token")

    assert "large-v3" in page.profile_description.text()
    with qtbot.waitSignal(page.start_requested) as started:
        qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]

    assert started.args == [
        session.session_id,
        TranscriptionProfile.ACCURATE.value,
        "en",
        "Akato, WASAPI",
        True,
        True,
        2,
        4,
        True,
        "temporary-token",
    ]
    assert page.diarization_token_input.text() == ""


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


def test_transcription_page_shows_persisted_model_download_progress(qtbot: QtBot) -> None:
    page = TranscriptionPage()
    qtbot.addWidget(page)
    session = _recorded_session()
    job = TranscriptionJob.new(session.session_id, profile=TranscriptionProfile.FAST)
    job = job.transition(TranscriptionJobState.PREPARING)
    job = job.with_model_download_progress(128 * 1024 * 1024, 512 * 1024 * 1024)

    page.load_session(session)
    page.show_job(job)

    assert page.progress_bar.value() == 25
    assert "small speech model" in page.progress_title.text()
    assert "128.0 of 512.0 MiB" in page.progress_detail.text()
    assert "models" in page.progress_detail.text()


def test_transcription_page_shows_indeterminate_diarization_and_fallback_warning(
    qtbot: QtBot,
) -> None:
    page = TranscriptionPage()
    qtbot.addWidget(page)
    session = _recorded_session()
    job = TranscriptionJob.new(
        session.session_id,
        separate_remote_speakers=True,
        min_remote_speakers=1,
        max_remote_speakers=3,
    )
    job = job.transition(TranscriptionJobState.PREPARING)
    job = job.with_progress(2_000, 2_000)
    job = job.transition(TranscriptionJobState.TRANSCRIBING)
    job = job.transition(TranscriptionJobState.DIARIZING)

    page.load_session(session, job)
    page.show_job(job)

    assert page.progress_bar.minimum() == 0
    assert page.progress_bar.maximum() == 0
    assert "Separating remote speakers" in page.progress_title.text()
    assert page.separate_remote_speakers_checkbox.isChecked()
    assert page.min_remote_speakers.value() == 1
    assert page.max_remote_speakers.value() == 3

    completed = job.with_warning("Remote-speaker model is unavailable").transition(
        TranscriptionJobState.COMPLETED
    )
    page.show_job(completed)

    assert page.progress_bar.maximum() == 100
    assert "model is unavailable" in page.previous_status.text()

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from meeting_transcriber.app.review_service import ReviewSnapshot
from meeting_transcriber.domain.review import TranscriptReview
from meeting_transcriber.domain.session import MeetingSession, SessionState
from meeting_transcriber.domain.transcript import (
    TranscriptDocument,
    TranscriptionProfile,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
)
from meeting_transcriber.ui.review_page import TranscriptReviewPage

SESSION_ID = "6039b71d-57fd-43e7-b78b-bdbb7a7b3498"
RUN_ID = "d4d0baef-a334-4d73-a7fe-1b060965ab42"
SEGMENT_ID = "371e9cee-8599-4c6d-b42d-d1349be38d74"
SECOND_SEGMENT_ID = "bd31414d-e1ab-4249-9be5-bf408bd6e43f"
START = datetime(2026, 8, 12, 5, 0, tzinfo=UTC)


def _session() -> MeetingSession:
    session = MeetingSession.new("Review UI", session_id=SESSION_ID, now=START)
    session = session.confirm_consent(at=START + timedelta(seconds=1))
    session = session.transition(SessionState.RECORDING, at=START + timedelta(seconds=2))
    return session.transition(SessionState.RECORDED, at=START + timedelta(minutes=1))


def _snapshot() -> ReviewSnapshot:
    transcript = TranscriptDocument.new(
        SESSION_ID,
        run_id=RUN_ID,
        language="en",
        engine="test",
        model="medium",
        profile=TranscriptionProfile.BALANCED,
        created_at=START,
        speakers=(
            TranscriptSpeaker("local", "You", TranscriptSource.MICROPHONE),
            TranscriptSpeaker("remote", "Remote speakers", TranscriptSource.SYSTEM_AUDIO),
            TranscriptSpeaker("remote-2", "Remote speaker 2", TranscriptSource.SYSTEM_AUDIO),
        ),
        segments=(
            TranscriptSegment(
                SEGMENT_ID,
                1_000,
                2_000,
                "remote",
                "Project at less",
                TranscriptSource.SYSTEM_AUDIO,
                confidence=0.8,
            ),
            TranscriptSegment(
                SECOND_SEGMENT_ID,
                1_500,
                2_500,
                "remote-2",
                "A second voice",
                TranscriptSource.SYSTEM_AUDIO,
                confidence=0.6,
            ),
        ),
    )
    review = TranscriptReview.new(transcript, at=START)
    return ReviewSnapshot(transcript, review, transcript, Path("meeting-notes.md"))


def test_review_page_scrolls_instead_of_compressing_editors(qtbot: QtBot) -> None:
    page = TranscriptReviewPage()
    qtbot.addWidget(page)
    page.resize(710, 600)
    page.load_snapshot(_session(), _snapshot())
    page.show()
    qtbot.wait(50)

    vertical_scroll_bar = page.scroll_area.verticalScrollBar()
    assert vertical_scroll_bar.maximum() > 0
    assert vertical_scroll_bar.value() == vertical_scroll_bar.minimum()
    assert page.scroll_area.horizontalScrollBar().maximum() == 0
    assert page.save_segment_button.height() >= page.save_segment_button.sizeHint().height()


def test_review_page_emits_explicit_speaker_and_segment_edits(qtbot: QtBot) -> None:
    page = TranscriptReviewPage()
    qtbot.addWidget(page)
    page.load_snapshot(_session(), _snapshot())
    page.speaker_combo.setCurrentIndex(1)
    page.speaker_name_input.setText("Morgan")

    with qtbot.waitSignal(page.rename_requested) as renamed:
        qtbot.mouseClick(page.save_speaker_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]
    assert renamed.args == [SESSION_ID, "remote", "Morgan"]

    page.segment_text_edit.setPlainText("Project Atlas")
    with qtbot.waitSignal(page.correction_requested) as corrected:
        qtbot.mouseClick(page.save_segment_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]
    assert corrected.args == [SESSION_ID, SEGMENT_ID, "Project Atlas"]

    page.segment_speaker_combo.setCurrentIndex(page.segment_speaker_combo.findData("remote-2"))
    with qtbot.waitSignal(page.assignment_requested) as assigned:
        qtbot.mouseClick(page.save_assignment_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]
    assert assigned.args == [SESSION_ID, SEGMENT_ID, "remote-2"]

    page.summary_edit.setPlainText("Launch review")
    page.decisions_edit.setPlainText("Ship Friday\nKeep processing local")
    page.action_items_edit.setPlainText("Morgan: publish notes")
    with qtbot.waitSignal(page.structured_notes_requested) as structured:
        qtbot.mouseClick(  # type: ignore[no-untyped-call]
            page.save_structured_notes_button,
            Qt.MouseButton.LeftButton,
        )
    assert structured.args == [
        SESSION_ID,
        "Launch review",
        "Ship Friday\nKeep processing local",
        "Morgan: publish notes",
    ]


def test_review_page_labels_overlap_and_low_confidence_without_color_only_cues(
    qtbot: QtBot,
) -> None:
    page = TranscriptReviewPage()
    qtbot.addWidget(page)
    page.load_snapshot(_session(), _snapshot())

    assert "OVERLAP" in page.segment_list.item(0).text()
    assert "LOW CONFIDENCE" not in page.segment_list.item(0).text()
    assert "OVERLAP" in page.segment_list.item(1).text()
    assert "LOW CONFIDENCE" in page.segment_list.item(1).text()
    assert "below 70%" in page.segment_list.item(1).toolTip()


def test_review_page_reset_controls_restore_model_values(qtbot: QtBot) -> None:
    snapshot = _snapshot()
    source = snapshot.source_transcript
    review = snapshot.review.rename_speaker(source, "remote", "Morgan")
    review = review.correct_segment(source, SEGMENT_ID, "Project Atlas")
    review = review.assign_segment(source, SEGMENT_ID, "remote-2")
    review = review.update_structured_notes(
        source,
        "Launch review",
        ("Ship Friday",),
        ("Morgan: publish notes",),
    )
    corrected = ReviewSnapshot(source, review, review.apply(source), Path("meeting-notes.md"))
    page = TranscriptReviewPage()
    qtbot.addWidget(page)
    page.load_snapshot(_session(), corrected, selected_speaker_id="remote")

    qtbot.mouseClick(page.reset_speaker_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]
    qtbot.mouseClick(page.reset_segment_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]
    qtbot.mouseClick(page.reset_assignment_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]

    assert page.speaker_name_input.text() == "Remote speakers"
    assert page.segment_text_edit.toPlainText() == "Project at less"
    assert page.segment_speaker_combo.currentData() == "remote"
    assert page.summary_edit.toPlainText() == "Launch review"
    assert page.decisions_edit.toPlainText() == "Ship Friday"
    assert page.action_items_edit.toPlainText() == "Morgan: publish notes"
    assert "revision 4" in page.review_status.text()

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from meeting_transcriber.app.review_service import ReviewSnapshot
from meeting_transcriber.domain.session import MeetingSession
from meeting_transcriber.domain.transcript import TranscriptSegment, TranscriptSpeaker
from meeting_transcriber.ui.scrolling import create_scrollable_page, reset_scroll_position


def _label(text: str, object_name: str | None = None, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if object_name is not None:
        label.setObjectName(object_name)
    label.setWordWrap(wrap)
    if wrap:
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        label.setMinimumWidth(0)
    return label


class TranscriptReviewPage(QWidget):
    rename_requested = Signal(str, str, str)
    correction_requested = Signal(str, str, str)
    assignment_requested = Signal(str, str, str)
    structured_notes_requested = Signal(str, str, str, str)
    open_notes_requested = Signal(str)
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._session_id: str | None = None
        self._snapshot: ReviewSnapshot | None = None
        self._source_speakers: dict[str, TranscriptSpeaker] = {}
        self._source_segments: dict[str, TranscriptSegment] = {}

        root, self.scroll_area = create_scrollable_page(
            self,
            accessible_name="Transcript review and meeting notes",
            margins=(38, 28, 38, 28),
            spacing=13,
        )
        root.addWidget(_label("TRANSCRIPT REVIEW", "eyebrow"))
        self.meeting_title = _label("Review meeting", "pageTitle", wrap=True)
        root.addWidget(self.meeting_title)
        root.addWidget(
            _label(
                "Corrections update the editable Markdown notes while the original model "
                "transcript and every saved review revision remain available locally.",
                "muted",
                wrap=True,
            )
        )

        speaker_card = QFrame()
        speaker_card.setObjectName("recordingCard")
        speaker_layout = QVBoxLayout(speaker_card)
        speaker_layout.setContentsMargins(20, 16, 20, 18)
        speaker_layout.setSpacing(9)
        speaker_layout.addWidget(_label("Speaker/source label", "sectionTitle"))
        speaker_row = QHBoxLayout()
        self.speaker_combo = QComboBox()
        self.speaker_combo.setAccessibleName("Speaker or audio source to rename")
        self.speaker_combo.currentIndexChanged.connect(self._speaker_changed)
        speaker_row.addWidget(self.speaker_combo, 1)
        self.speaker_name_input = QLineEdit()
        self.speaker_name_input.setAccessibleName("Reviewed speaker display name")
        self.speaker_name_input.setPlaceholderText("Display name")
        speaker_row.addWidget(self.speaker_name_input, 1)
        self.reset_speaker_button = QPushButton("Reset name")
        self.reset_speaker_button.clicked.connect(self._reset_speaker_name)
        speaker_row.addWidget(self.reset_speaker_button)
        self.save_speaker_button = QPushButton("Save name")
        self.save_speaker_button.setObjectName("primaryButton")
        self.save_speaker_button.clicked.connect(self._emit_rename)
        speaker_row.addWidget(self.save_speaker_button)
        speaker_layout.addLayout(speaker_row)
        root.addWidget(speaker_card)

        notes_card = QFrame()
        notes_card.setObjectName("recordingCard")
        notes_layout = QVBoxLayout(notes_card)
        notes_layout.setContentsMargins(20, 16, 20, 18)
        notes_layout.setSpacing(9)
        notes_layout.addWidget(_label("Reviewed meeting notes", "sectionTitle"))
        notes_layout.addWidget(
            _label(
                "Write a summary and put each decision or action item on its own line.",
                "muted",
                wrap=True,
            )
        )
        notes_fields = QHBoxLayout()
        summary_column = QVBoxLayout()
        summary_column.addWidget(_label("Summary", "muted"))
        self.summary_edit = QPlainTextEdit()
        self.summary_edit.setAccessibleName("Reviewed meeting summary")
        self.summary_edit.setPlaceholderText("What was discussed and why?")
        self.summary_edit.setMaximumHeight(100)
        summary_column.addWidget(self.summary_edit)
        notes_fields.addLayout(summary_column, 1)
        decisions_column = QVBoxLayout()
        decisions_column.addWidget(_label("Decisions — one per line", "muted"))
        self.decisions_edit = QPlainTextEdit()
        self.decisions_edit.setAccessibleName("Reviewed meeting decisions")
        self.decisions_edit.setPlaceholderText("Approved the launch date")
        self.decisions_edit.setMaximumHeight(100)
        decisions_column.addWidget(self.decisions_edit)
        notes_fields.addLayout(decisions_column, 1)
        actions_column = QVBoxLayout()
        actions_column.addWidget(_label("Action items — one per line", "muted"))
        self.action_items_edit = QPlainTextEdit()
        self.action_items_edit.setAccessibleName("Reviewed meeting action items")
        self.action_items_edit.setPlaceholderText("Morgan: share the revised plan by Friday")
        self.action_items_edit.setMaximumHeight(100)
        actions_column.addWidget(self.action_items_edit)
        notes_fields.addLayout(actions_column, 1)
        notes_layout.addLayout(notes_fields)
        notes_actions = QHBoxLayout()
        notes_actions.addStretch()
        self.save_structured_notes_button = QPushButton("Save reviewed notes")
        self.save_structured_notes_button.setObjectName("primaryButton")
        self.save_structured_notes_button.clicked.connect(self._emit_structured_notes)
        notes_actions.addWidget(self.save_structured_notes_button)
        notes_layout.addLayout(notes_actions)
        root.addWidget(notes_card)

        transcript_card = QFrame()
        transcript_card.setObjectName("historyCard")
        transcript_layout = QVBoxLayout(transcript_card)
        transcript_layout.setContentsMargins(20, 16, 20, 18)
        transcript_layout.setSpacing(9)
        transcript_layout.addWidget(_label("Conversation segments", "sectionTitle"))
        segment_row = QHBoxLayout()
        self.segment_list = QListWidget()
        self.segment_list.setAccessibleName("Transcript segments")
        self.segment_list.itemSelectionChanged.connect(self._segment_changed)
        segment_row.addWidget(self.segment_list, 1)
        editor_column = QVBoxLayout()
        editor_column.addWidget(_label("Assign selected speaker", "muted"))
        self.segment_speaker_combo = QComboBox()
        self.segment_speaker_combo.setAccessibleName("Speaker assigned to selected segment")
        editor_column.addWidget(self.segment_speaker_combo)
        assignment_actions = QHBoxLayout()
        self.reset_assignment_button = QPushButton("Reset model speaker")
        self.reset_assignment_button.clicked.connect(self._reset_segment_speaker)
        assignment_actions.addWidget(self.reset_assignment_button)
        assignment_actions.addStretch()
        self.save_assignment_button = QPushButton("Save speaker assignment")
        self.save_assignment_button.clicked.connect(self._emit_assignment)
        assignment_actions.addWidget(self.save_assignment_button)
        editor_column.addLayout(assignment_actions)
        editor_column.addWidget(_label("Correct selected text", "muted"))
        self.segment_text_edit = QPlainTextEdit()
        self.segment_text_edit.setAccessibleName("Corrected transcript segment text")
        self.segment_text_edit.setPlaceholderText("Select a transcript segment to review")
        editor_column.addWidget(self.segment_text_edit, 1)
        editor_actions = QHBoxLayout()
        self.reset_segment_button = QPushButton("Reset text")
        self.reset_segment_button.clicked.connect(self._reset_segment_text)
        editor_actions.addWidget(self.reset_segment_button)
        editor_actions.addStretch()
        self.save_segment_button = QPushButton("Save correction")
        self.save_segment_button.setObjectName("primaryButton")
        self.save_segment_button.clicked.connect(self._emit_correction)
        editor_actions.addWidget(self.save_segment_button)
        editor_column.addLayout(editor_actions)
        segment_row.addLayout(editor_column, 1)
        transcript_layout.addLayout(segment_row)
        root.addWidget(transcript_card, 1)

        self.review_status = _label("No review loaded.", "muted", wrap=True)
        root.addWidget(self.review_status)
        actions = QHBoxLayout()
        self.back_button = QPushButton("Back to history")
        self.back_button.clicked.connect(self.back_requested.emit)
        actions.addWidget(self.back_button)
        actions.addStretch()
        self.open_notes_button = QPushButton("Open meeting notes")
        self.open_notes_button.clicked.connect(self._emit_open_notes)
        actions.addWidget(self.open_notes_button)
        root.addLayout(actions)
        self._set_editing_enabled(False)

    def load_snapshot(
        self,
        session: MeetingSession,
        snapshot: ReviewSnapshot,
        *,
        selected_speaker_id: str | None = None,
        selected_segment_id: str | None = None,
        saved_message: str | None = None,
    ) -> None:
        self._session_id = session.session_id
        self._snapshot = snapshot
        self.meeting_title.setText(session.title)
        self._source_speakers = {
            speaker.speaker_id: speaker for speaker in snapshot.source_transcript.speakers
        }
        self._source_segments = {
            segment.segment_id: segment for segment in snapshot.source_transcript.segments
        }
        reviewed_speakers = {
            speaker.speaker_id: speaker for speaker in snapshot.reviewed_transcript.speakers
        }
        self.speaker_combo.blockSignals(True)
        self.speaker_combo.clear()
        for speaker in snapshot.reviewed_transcript.speakers:
            self.speaker_combo.addItem(
                f"{speaker.display_name} — {speaker.source.value.replace('_', ' ').title()}",
                speaker.speaker_id,
            )
        speaker_index = self.speaker_combo.findData(selected_speaker_id)
        self.speaker_combo.setCurrentIndex(max(0, speaker_index))
        self.speaker_combo.blockSignals(False)
        self._speaker_changed()

        structured_notes = snapshot.review.structured_notes
        self.summary_edit.setPlainText(structured_notes.summary if structured_notes else "")
        self.decisions_edit.setPlainText(
            "\n".join(structured_notes.decisions) if structured_notes else ""
        )
        self.action_items_edit.setPlainText(
            "\n".join(structured_notes.action_items) if structured_notes else ""
        )

        self.segment_list.clear()
        overlapping_ids = _overlapping_segment_ids(snapshot.reviewed_transcript.segments)
        for segment in snapshot.reviewed_transcript.segments:
            speaker = reviewed_speakers[segment.speaker_id]
            confidence = (
                f" · {segment.confidence * 100:.0f}%" if segment.confidence is not None else ""
            )
            cues = []
            if segment.segment_id in overlapping_ids:
                cues.append("OVERLAP")
            if segment.confidence is not None and segment.confidence < 0.70:
                cues.append("LOW CONFIDENCE")
            cue_text = f" · {' · '.join(cues)}" if cues else ""
            source_label = segment.source.value.replace("_", " ").title()
            item = QListWidgetItem(
                f"{_timestamp(segment.start_ms)} · {speaker.display_name} · {source_label}"
                f"{confidence}{cue_text}\n"
                f"{segment.text}"
            )
            item.setData(Qt.ItemDataRole.UserRole, segment.segment_id)
            if cues:
                item.setToolTip(
                    "Review cue(s): "
                    + ", ".join(cues).lower()
                    + ". Overlap means another segment occurs at the same time; low confidence "
                    "means the model confidence is below 70%."
                )
            self.segment_list.addItem(item)
        segment_index = self._segment_index(selected_segment_id)
        if self.segment_list.count():
            self.segment_list.setCurrentRow(max(0, segment_index))
        else:
            self._segment_changed()
        self._set_editing_enabled(True)
        review = snapshot.review
        detail = (
            f"Review revision {review.revision}: {len(review.speaker_names)} renamed label(s), "
            f"{len(review.segment_texts)} corrected segment(s), "
            f"{len(review.segment_speakers)} reassigned speaker(s), "
            f"{1 if review.structured_notes is not None else 0} reviewed note set(s)."
        )
        self.review_status.setText(f"{saved_message} {detail}" if saved_message else detail)
        reset_scroll_position(self.scroll_area)

    def selected_speaker_id(self) -> str | None:
        value = self.speaker_combo.currentData()
        return value if isinstance(value, str) else None

    def selected_segment_id(self) -> str | None:
        selected = self.segment_list.selectedItems()
        if not selected:
            return None
        value = selected[0].data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, str) else None

    def _speaker_changed(self, *_args: object) -> None:
        speaker_id = self.selected_speaker_id()
        if speaker_id is None or self._snapshot is None:
            self.speaker_name_input.clear()
            return
        speaker = next(
            item
            for item in self._snapshot.reviewed_transcript.speakers
            if item.speaker_id == speaker_id
        )
        self.speaker_name_input.setText(speaker.display_name)

    def _segment_changed(self) -> None:
        segment_id = self.selected_segment_id()
        if segment_id is None or self._snapshot is None:
            self.segment_text_edit.clear()
            self.segment_speaker_combo.clear()
            self.segment_speaker_combo.setEnabled(False)
            self.reset_assignment_button.setEnabled(False)
            self.save_assignment_button.setEnabled(False)
            self.segment_text_edit.setEnabled(False)
            self.reset_segment_button.setEnabled(False)
            self.save_segment_button.setEnabled(False)
            return
        segment = next(
            item
            for item in self._snapshot.reviewed_transcript.segments
            if item.segment_id == segment_id
        )
        self.segment_speaker_combo.blockSignals(True)
        self.segment_speaker_combo.clear()
        for speaker in self._snapshot.reviewed_transcript.speakers:
            if speaker.source == segment.source:
                self.segment_speaker_combo.addItem(speaker.display_name, speaker.speaker_id)
        assignment_index = self.segment_speaker_combo.findData(segment.speaker_id)
        self.segment_speaker_combo.setCurrentIndex(max(0, assignment_index))
        self.segment_speaker_combo.blockSignals(False)
        self.segment_speaker_combo.setEnabled(True)
        self.reset_assignment_button.setEnabled(True)
        self.save_assignment_button.setEnabled(True)
        self.segment_text_edit.setEnabled(True)
        self.reset_segment_button.setEnabled(True)
        self.save_segment_button.setEnabled(True)
        self.segment_text_edit.setPlainText(segment.text)

    def _reset_speaker_name(self) -> None:
        speaker_id = self.selected_speaker_id()
        source = self._source_speakers.get(speaker_id or "")
        if source is not None:
            self.speaker_name_input.setText(source.display_name)

    def _reset_segment_text(self) -> None:
        segment_id = self.selected_segment_id()
        source = self._source_segments.get(segment_id or "")
        if source is not None:
            self.segment_text_edit.setPlainText(source.text)

    def _reset_segment_speaker(self) -> None:
        segment_id = self.selected_segment_id()
        source = self._source_segments.get(segment_id or "")
        if source is not None:
            index = self.segment_speaker_combo.findData(source.speaker_id)
            if index >= 0:
                self.segment_speaker_combo.setCurrentIndex(index)

    def _emit_rename(self) -> None:
        session_id = self._session_id
        speaker_id = self.selected_speaker_id()
        if session_id is not None and speaker_id is not None:
            self.rename_requested.emit(
                session_id,
                speaker_id,
                self.speaker_name_input.text(),
            )

    def _emit_correction(self) -> None:
        session_id = self._session_id
        segment_id = self.selected_segment_id()
        if session_id is not None and segment_id is not None:
            self.correction_requested.emit(
                session_id,
                segment_id,
                self.segment_text_edit.toPlainText(),
            )

    def _emit_assignment(self) -> None:
        session_id = self._session_id
        segment_id = self.selected_segment_id()
        speaker_id = self.segment_speaker_combo.currentData()
        if session_id is not None and segment_id is not None and isinstance(speaker_id, str):
            self.assignment_requested.emit(session_id, segment_id, speaker_id)

    def _emit_structured_notes(self) -> None:
        if self._session_id is not None:
            self.structured_notes_requested.emit(
                self._session_id,
                self.summary_edit.toPlainText(),
                self.decisions_edit.toPlainText(),
                self.action_items_edit.toPlainText(),
            )

    def _emit_open_notes(self) -> None:
        if self._session_id is not None:
            self.open_notes_requested.emit(self._session_id)

    def _segment_index(self, segment_id: str | None) -> int:
        if segment_id is None:
            return 0
        for index in range(self.segment_list.count()):
            if self.segment_list.item(index).data(Qt.ItemDataRole.UserRole) == segment_id:
                return index
        return 0

    def _set_editing_enabled(self, enabled: bool) -> None:
        self.speaker_combo.setEnabled(enabled)
        self.speaker_name_input.setEnabled(enabled)
        self.reset_speaker_button.setEnabled(enabled)
        self.save_speaker_button.setEnabled(enabled)
        self.summary_edit.setEnabled(enabled)
        self.decisions_edit.setEnabled(enabled)
        self.action_items_edit.setEnabled(enabled)
        self.save_structured_notes_button.setEnabled(enabled)
        self.open_notes_button.setEnabled(enabled)


def _timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, _millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _overlapping_segment_ids(segments: tuple[TranscriptSegment, ...]) -> set[str]:
    overlapping: set[str] = set()
    ordered = sorted(segments, key=lambda segment: (segment.start_ms, segment.end_ms))
    for index, segment in enumerate(ordered):
        for candidate in ordered[index + 1 :]:
            if candidate.start_ms >= segment.end_ms:
                break
            if candidate.end_ms > segment.start_ms:
                overlapping.update((segment.segment_id, candidate.segment_id))
    return overlapping

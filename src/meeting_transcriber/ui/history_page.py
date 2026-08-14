from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from meeting_transcriber.domain.session import MeetingSession, SessionState


def _label(text: str, object_name: str | None = None, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if object_name is not None:
        label.setObjectName(object_name)
    label.setWordWrap(wrap)
    if wrap:
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        label.setMinimumWidth(0)
    return label


class HistoryPage(QWidget):
    refresh_requested = Signal()
    open_folder_requested = Signal(str)
    open_notes_requested = Signal(str)
    review_requested = Signal(str)
    recover_requested = Signal(str)
    transcribe_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._sessions: dict[str, MeetingSession] = {}
        self._recoverable_ids: frozenset[str] = frozenset()
        self._notes_ids: frozenset[str] = frozenset()
        self._transcript_ids: frozenset[str] = frozenset()

        root = QVBoxLayout(self)
        root.setContentsMargins(38, 32, 38, 32)
        root.setSpacing(16)
        header = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.addWidget(_label("MEETING HISTORY", "eyebrow"))
        header_text.addWidget(_label("Local recording sessions", "pageTitle"))
        header.addLayout(header_text)
        header.addStretch()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        root.addWidget(
            _label(
                "Open a session folder or recover finalized chunks from a recording that "
                "was interrupted before its state could be finalized.",
                "muted",
                wrap=True,
            )
        )

        card = QFrame()
        card.setObjectName("historyCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 20)
        card_layout.setSpacing(12)
        self.session_list = QListWidget()
        self.session_list.setAccessibleName("Meeting session history")
        self.session_list.itemSelectionChanged.connect(self._update_actions)
        card_layout.addWidget(self.session_list)
        self.selection_status = _label("Select a meeting session.", "muted", wrap=True)
        card_layout.addWidget(self.selection_status)

        actions = QGridLayout()
        actions.setHorizontalSpacing(12)
        actions.setVerticalSpacing(10)
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.setEnabled(False)
        self.open_folder_button.clicked.connect(self._emit_open_folder)
        actions.addWidget(self.open_folder_button, 0, 0)
        self.open_notes_button = QPushButton("Open notes")
        self.open_notes_button.setEnabled(False)
        self.open_notes_button.clicked.connect(self._emit_open_notes)
        actions.addWidget(self.open_notes_button, 0, 1)
        self.review_button = QPushButton("Review")
        self.review_button.setEnabled(False)
        self.review_button.clicked.connect(self._emit_review)
        actions.addWidget(self.review_button, 1, 0)
        self.recover_button = QPushButton("Recover audio")
        self.recover_button.setObjectName("primaryButton")
        self.recover_button.setEnabled(False)
        self.recover_button.clicked.connect(self._emit_recover)
        actions.addWidget(self.recover_button, 1, 1)
        self.transcribe_button = QPushButton("Transcribe offline")
        self.transcribe_button.setObjectName("primaryButton")
        self.transcribe_button.setEnabled(False)
        self.transcribe_button.clicked.connect(self._emit_transcribe)
        actions.addWidget(self.transcribe_button, 2, 0, 1, 2)
        for column in range(2):
            actions.setColumnStretch(column, 1)
        card_layout.addLayout(actions)
        root.addWidget(card)
        root.addStretch()

    def load_sessions(
        self,
        sessions: list[MeetingSession],
        recoverable_ids: frozenset[str],
        notes_ids: frozenset[str] = frozenset(),
        transcript_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._sessions = {session.session_id: session for session in sessions}
        self._recoverable_ids = recoverable_ids
        self._notes_ids = notes_ids
        self._transcript_ids = transcript_ids
        self.session_list.clear()
        for session in sessions:
            updated = session.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
            state = session.state.value.replace("_", " ").title()
            markers: list[str] = []
            if session.session_id in recoverable_ids:
                markers.append("recoverable audio")
            if session.session_id in notes_ids:
                markers.append("notes ready")
            suffix = "" if not markers else f" • {' • '.join(markers)}"
            item = QListWidgetItem(f"{session.title}\n{updated} • {state}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, session.session_id)
            self.session_list.addItem(item)
        if sessions:
            self.session_list.setCurrentRow(0)
        else:
            self._update_actions()
            self.selection_status.setText("No local meeting sessions yet.")

    def selected_session_id(self) -> str | None:
        selected_items = self.session_list.selectedItems()
        if not selected_items:
            return None
        item = selected_items[0]
        session_id = item.data(Qt.ItemDataRole.UserRole)
        return session_id if isinstance(session_id, str) else None

    def _update_actions(self) -> None:
        session_id = self.selected_session_id()
        session = self._sessions.get(session_id) if session_id is not None else None
        self.open_folder_button.setEnabled(session is not None)
        has_notes = session is not None and session.session_id in self._notes_ids
        self.open_notes_button.setEnabled(has_notes)
        has_transcript = session is not None and session.session_id in self._transcript_ids
        self.review_button.setEnabled(has_transcript)
        can_recover = (
            session is not None
            and session.state is SessionState.INTERRUPTED
            and session.session_id in self._recoverable_ids
        )
        self.recover_button.setEnabled(can_recover)
        can_transcribe = session is not None and session.state in {
            SessionState.RECORDED,
            SessionState.READY,
            SessionState.EXPORTED,
        }
        self.transcribe_button.setEnabled(can_transcribe)
        if session is None:
            self.selection_status.setText("Select a meeting session.")
        elif can_recover:
            self.selection_status.setText(
                "Finalized WAV chunks were found. Recovery will make this session ready for "
                "the processing milestone."
            )
        elif session.state is SessionState.INTERRUPTED:
            self.selection_status.setText(
                "This session is interrupted, but no finalized WAV chunks are available."
            )
        elif has_notes and has_transcript:
            self.selection_status.setText(
                "Meeting notes are ready. Review speaker labels or transcript text in the app."
            )
        elif has_notes:
            self.selection_status.setText("Structured Markdown meeting notes are ready to open.")
        elif has_transcript:
            self.selection_status.setText("The transcript is ready for speaker and text review.")
        elif can_transcribe:
            self.selection_status.setText(
                "Finalized audio is available for private offline transcription."
            )
        else:
            self.selection_status.setText(f"Session state: {session.state.value}.")

    def _emit_open_folder(self) -> None:
        session_id = self.selected_session_id()
        if session_id is not None:
            self.open_folder_requested.emit(session_id)

    def _emit_open_notes(self) -> None:
        session_id = self.selected_session_id()
        if session_id is not None and self.open_notes_button.isEnabled():
            self.open_notes_requested.emit(session_id)

    def _emit_review(self) -> None:
        session_id = self.selected_session_id()
        if session_id is not None and self.review_button.isEnabled():
            self.review_requested.emit(session_id)

    def _emit_recover(self) -> None:
        session_id = self.selected_session_id()
        if session_id is not None and self.recover_button.isEnabled():
            self.recover_requested.emit(session_id)

    def _emit_transcribe(self) -> None:
        session_id = self.selected_session_id()
        if session_id is not None and self.transcribe_button.isEnabled():
            self.transcribe_requested.emit(session_id)

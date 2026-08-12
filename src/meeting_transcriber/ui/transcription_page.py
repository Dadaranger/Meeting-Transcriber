from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from meeting_transcriber.domain.session import MeetingSession
from meeting_transcriber.domain.transcript import (
    TranscriptionJob,
    TranscriptionJobState,
    TranscriptionProfile,
)
from meeting_transcriber.processing.engine import MODEL_PROFILES


def _label(text: str, object_name: str | None = None, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if object_name is not None:
        label.setObjectName(object_name)
    label.setWordWrap(wrap)
    return label


class TranscriptionPage(QWidget):
    start_requested = Signal(str, str, str, str, bool)
    cancel_requested = Signal()
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._session_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(38, 32, 38, 32)
        root.setSpacing(16)
        root.addWidget(_label("OFFLINE TRANSCRIPTION", "eyebrow"))
        root.addWidget(_label("Create a timestamped local transcript", "pageTitle"))
        root.addWidget(
            _label(
                "Audio and generated text stay on this computer. A model may need to be "
                "downloaded once before later runs can work fully offline.",
                "muted",
                wrap=True,
            )
        )

        self.setup_card = QFrame()
        self.setup_card.setObjectName("recordingCard")
        setup_layout = QVBoxLayout(self.setup_card)
        setup_layout.setContentsMargins(24, 22, 24, 24)
        setup_layout.setSpacing(12)
        setup_layout.addWidget(_label("Meeting", "muted"))
        self.meeting_title = _label("No meeting selected", "sectionTitle", wrap=True)
        setup_layout.addWidget(self.meeting_title)

        setup_layout.addWidget(_label("Accuracy profile", "muted"))
        self.profile_combo = QComboBox()
        self.profile_combo.setAccessibleName("Transcription accuracy profile")
        for profile, label in (
            (TranscriptionProfile.FAST, "Fast — smaller model"),
            (TranscriptionProfile.BALANCED, "Balanced — recommended"),
            (TranscriptionProfile.ACCURATE, "Best accuracy — largest model"),
        ):
            self.profile_combo.addItem(label, profile.value)
        self.profile_combo.setCurrentIndex(1)
        self.profile_combo.currentIndexChanged.connect(self._update_profile_description)
        setup_layout.addWidget(self.profile_combo)
        self.profile_description = _label("", "muted", wrap=True)
        setup_layout.addWidget(self.profile_description)

        setup_layout.addWidget(_label("Language", "muted"))
        self.language_combo = QComboBox()
        self.language_combo.setEditable(True)
        self.language_combo.setAccessibleName("Meeting language or automatic detection")
        for label, code in (
            ("Auto detect", ""),
            ("English", "en"),
            ("Chinese", "zh"),
            ("Spanish", "es"),
            ("French", "fr"),
            ("German", "de"),
            ("Japanese", "ja"),
            ("Korean", "ko"),
        ):
            self.language_combo.addItem(label, code)
        setup_layout.addWidget(self.language_combo)

        setup_layout.addWidget(_label("Names and technical terms (optional)", "muted"))
        self.hotwords_input = QLineEdit()
        self.hotwords_input.setPlaceholderText("Example: Akato, WASAPI, Project Atlas")
        self.hotwords_input.setAccessibleName("Transcription vocabulary hints")
        setup_layout.addWidget(self.hotwords_input)

        self.allow_download_checkbox = QCheckBox(
            "Allow this run to download the selected speech model if it is not cached"
        )
        self.allow_download_checkbox.setAccessibleName("Allow speech model download")
        setup_layout.addWidget(self.allow_download_checkbox)
        self.previous_status = _label("", "muted", wrap=True)
        setup_layout.addWidget(self.previous_status)

        setup_actions = QHBoxLayout()
        self.back_button = QPushButton("Back to history")
        self.back_button.clicked.connect(self.back_requested.emit)
        setup_actions.addWidget(self.back_button)
        setup_actions.addStretch()
        self.start_button = QPushButton("Start offline transcription")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self._emit_start)
        setup_actions.addWidget(self.start_button)
        setup_layout.addLayout(setup_actions)
        root.addWidget(self.setup_card)

        self.progress_card = QFrame()
        self.progress_card.setObjectName("recordingCard")
        progress_layout = QVBoxLayout(self.progress_card)
        progress_layout.setContentsMargins(24, 22, 24, 24)
        progress_layout.setSpacing(14)
        self.progress_title = _label("Preparing audio", "sectionTitle", wrap=True)
        progress_layout.addWidget(self.progress_title)
        self.progress_detail = _label("Persisting local processing state…", "muted", wrap=True)
        progress_layout.addWidget(self.progress_detail)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setAccessibleName("Offline transcription progress")
        progress_layout.addWidget(self.progress_bar)
        progress_actions = QHBoxLayout()
        progress_actions.addStretch()
        self.cancel_button = QPushButton("Cancel transcription")
        self.cancel_button.setObjectName("dangerButton")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        progress_actions.addWidget(self.cancel_button)
        progress_layout.addLayout(progress_actions)
        self.progress_card.hide()
        root.addWidget(self.progress_card)
        root.addStretch()
        self._update_profile_description()

    def load_session(self, session: MeetingSession, job: TranscriptionJob | None = None) -> None:
        self._session_id = session.session_id
        self.meeting_title.setText(session.title)
        self.setup_card.show()
        self.progress_card.hide()
        self.start_button.setText("Start offline transcription")
        self.previous_status.setText("")
        if job is not None:
            self.profile_combo.setCurrentIndex(
                max(0, self.profile_combo.findData(job.profile.value))
            )
            if job.language:
                language_index = self.language_combo.findData(job.language)
                if language_index >= 0:
                    self.language_combo.setCurrentIndex(language_index)
                else:
                    self.language_combo.setEditText(job.language)
            if job.state in {TranscriptionJobState.FAILED, TranscriptionJobState.CANCELLED}:
                self.start_button.setText("Retry offline transcription")
                self.previous_status.setText(
                    job.error
                    or "The previous transcription was cancelled. Prepared audio is reusable."
                )

    def show_job(self, job: TranscriptionJob) -> None:
        if job.state in {
            TranscriptionJobState.PENDING,
            TranscriptionJobState.PREPARING,
            TranscriptionJobState.TRANSCRIBING,
        }:
            self.setup_card.hide()
            self.progress_card.show()
            self.progress_bar.setValue(round(job.progress * 100))
            if job.state is TranscriptionJobState.TRANSCRIBING:
                self.progress_title.setText("Transcribing locally")
                self.progress_detail.setText(
                    f"Processed {job.processed_audio_ms / 1_000:.1f} of "
                    f"{job.total_audio_ms / 1_000:.1f} source-seconds."
                )
            else:
                self.progress_title.setText("Preparing audio and model")
                self.progress_detail.setText(
                    "Validating chunks and loading the selected local speech model."
                )
            return
        self.progress_card.hide()
        self.setup_card.show()
        if job.state is TranscriptionJobState.COMPLETED:
            self.previous_status.setText(
                "Transcript complete. Open the meeting folder to inspect transcript.json."
            )
            self.start_button.setText("Transcribe again")
        else:
            self.previous_status.setText(job.error or "Transcription was cancelled.")
            self.start_button.setText("Retry offline transcription")

    def set_cancelling(self) -> None:
        self.cancel_button.setEnabled(False)
        self.progress_detail.setText("Cancellation requested; waiting for a safe model boundary…")

    def reset_cancel_control(self) -> None:
        self.cancel_button.setEnabled(True)

    def _update_profile_description(self, *_args: object) -> None:
        raw_profile = self.profile_combo.currentData()
        try:
            profile = TranscriptionProfile(str(raw_profile))
        except ValueError:
            return
        settings = MODEL_PROFILES[profile]
        self.profile_description.setText(
            f"Model: {settings.model_name}; decoding beam: {settings.beam_size}. "
            "Larger profiles need more memory and processing time."
        )

    def _language(self) -> str:
        index = self.language_combo.currentIndex()
        selected = self.language_combo.currentData()
        if (
            index >= 0
            and isinstance(selected, str)
            and self.language_combo.currentText() == self.language_combo.itemText(index)
        ):
            return selected
        return self.language_combo.currentText().strip()

    def _emit_start(self) -> None:
        profile = self.profile_combo.currentData()
        if self._session_id is None or not isinstance(profile, str):
            return
        self.start_requested.emit(
            self._session_id,
            profile,
            self._language(),
            self.hotwords_input.text().strip(),
            self.allow_download_checkbox.isChecked(),
        )

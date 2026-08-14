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
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from meeting_transcriber.domain.session import MeetingSession
from meeting_transcriber.domain.transcript import (
    TranscriptionJob,
    TranscriptionJobState,
    TranscriptionProfile,
)
from meeting_transcriber.infrastructure.paths import default_models_directory
from meeting_transcriber.processing.engine import MODEL_PROFILES
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


class TranscriptionPage(QWidget):
    start_requested = Signal(str, str, str, str, bool, bool, int, int, bool, str)
    cancel_requested = Signal()
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._session_id: str | None = None

        root, self.scroll_area = create_scrollable_page(
            self,
            accessible_name="Offline transcription setup and progress",
            margins=(38, 32, 38, 32),
            spacing=16,
        )
        root.addWidget(_label("OFFLINE TRANSCRIPTION", "eyebrow"))
        root.addWidget(_label("Create a timestamped local transcript", "pageTitle", wrap=True))
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
            "Allow this run to download the selected speech model\nif it is not cached"
        )
        self.allow_download_checkbox.setAccessibleName("Allow speech model download")
        setup_layout.addWidget(self.allow_download_checkbox)

        self.separate_remote_speakers_checkbox = QCheckBox(
            "Separate individual voices in system audio (optional; slower)"
        )
        self.separate_remote_speakers_checkbox.setAccessibleName(
            "Separate individual remote speakers"
        )
        self.separate_remote_speakers_checkbox.toggled.connect(self._update_diarization_controls)
        setup_layout.addWidget(self.separate_remote_speakers_checkbox)
        self.diarization_description = _label(
            "Uses pyannote Community-1 locally after a one-time gated model download. "
            '<a href="https://huggingface.co/pyannote/speaker-diarization-community-1">'
            "Accept the model terms on Hugging Face</a>, then enter a temporary read token below.",
            "muted",
            wrap=True,
        )
        self.diarization_description.setOpenExternalLinks(True)
        setup_layout.addWidget(self.diarization_description)

        speaker_limits = QHBoxLayout()
        speaker_limits.addWidget(_label("Remote speakers", "muted"))
        speaker_limits.addStretch()
        speaker_limits.addWidget(_label("Minimum", "muted"))
        self.min_remote_speakers = QSpinBox()
        self.min_remote_speakers.setRange(0, 20)
        self.min_remote_speakers.setSpecialValueText("Auto")
        self.min_remote_speakers.setAccessibleName("Minimum remote speaker count")
        speaker_limits.addWidget(self.min_remote_speakers)
        speaker_limits.addWidget(_label("Maximum", "muted"))
        self.max_remote_speakers = QSpinBox()
        self.max_remote_speakers.setRange(0, 20)
        self.max_remote_speakers.setSpecialValueText("Auto")
        self.max_remote_speakers.setAccessibleName("Maximum remote speaker count")
        speaker_limits.addWidget(self.max_remote_speakers)
        setup_layout.addLayout(speaker_limits)

        self.allow_diarization_download_checkbox = QCheckBox(
            "Allow this run to download the remote-speaker model\nif it is not cached"
        )
        self.allow_diarization_download_checkbox.setAccessibleName(
            "Allow remote speaker model download"
        )
        self.allow_diarization_download_checkbox.toggled.connect(self._update_diarization_controls)
        setup_layout.addWidget(self.allow_diarization_download_checkbox)
        self.diarization_token_input = QLineEdit()
        self.diarization_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.diarization_token_input.setPlaceholderText(
            "Temporary Hugging Face read token (used for this run only)"
        )
        self.diarization_token_input.setAccessibleName("Temporary Hugging Face access token")
        setup_layout.addWidget(self.diarization_token_input)
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
        self._update_diarization_controls()

    def load_session(self, session: MeetingSession, job: TranscriptionJob | None = None) -> None:
        self._session_id = session.session_id
        self.meeting_title.setText(session.title)
        self.setup_card.show()
        self.progress_card.hide()
        self.start_button.setText("Start offline transcription")
        self.previous_status.setText("")
        self.diarization_token_input.clear()
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
            self.separate_remote_speakers_checkbox.setChecked(job.separate_remote_speakers)
            self.min_remote_speakers.setValue(job.min_remote_speakers or 0)
            self.max_remote_speakers.setValue(job.max_remote_speakers or 0)
            if job.state in {TranscriptionJobState.FAILED, TranscriptionJobState.CANCELLED}:
                self.start_button.setText("Retry offline transcription")
                self.previous_status.setText(
                    job.error
                    or "The previous transcription was cancelled. Prepared audio is reusable."
                )
            elif job.state is TranscriptionJobState.COMPLETED and job.warning:
                self.previous_status.setText(job.warning)
        else:
            self.separate_remote_speakers_checkbox.setChecked(False)
            self.min_remote_speakers.setValue(0)
            self.max_remote_speakers.setValue(0)
        self.allow_diarization_download_checkbox.setChecked(False)
        self._update_diarization_controls()
        reset_scroll_position(self.scroll_area)

    def show_job(self, job: TranscriptionJob) -> None:
        reset_scroll_position(self.scroll_area)
        if job.state in {
            TranscriptionJobState.PENDING,
            TranscriptionJobState.PREPARING,
            TranscriptionJobState.TRANSCRIBING,
            TranscriptionJobState.DIARIZING,
        }:
            self.setup_card.hide()
            self.progress_card.show()
            if job.state is TranscriptionJobState.DIARIZING:
                self.progress_bar.setRange(0, 0)
                self.progress_title.setText("Separating remote speakers locally")
                self.progress_detail.setText(
                    "Analyzing system audio. This optional stage can take longer than "
                    "transcription and cancellation waits for a safe model boundary."
                )
            elif job.state is TranscriptionJobState.TRANSCRIBING:
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(round(job.progress * 100))
                self.progress_title.setText("Transcribing locally")
                self.progress_detail.setText(
                    f"Processed {job.processed_audio_ms / 1_000:.1f} of "
                    f"{job.total_audio_ms / 1_000:.1f} source-seconds."
                )
            else:
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(round(job.progress * 100))
                if job.model_total_bytes:
                    downloaded = job.model_downloaded_bytes / (1024 * 1024)
                    total = job.model_total_bytes / (1024 * 1024)
                    model_name = MODEL_PROFILES[job.profile].model_name
                    self.progress_title.setText(f"Downloading {model_name} speech model")
                    self.progress_detail.setText(
                        f"Cached {downloaded:.1f} of {total:.1f} MiB in "
                        f"{default_models_directory()}."
                    )
                else:
                    self.progress_title.setText("Preparing audio and model")
                    self.progress_detail.setText(
                        "Checking the selected speech model and validating prepared audio."
                    )
            return
        self.progress_card.hide()
        self.progress_bar.setRange(0, 100)
        self.setup_card.show()
        if job.state is TranscriptionJobState.COMPLETED:
            self.previous_status.setText(
                job.warning
                or "Transcript complete. Open meeting-notes.md from History to review or edit it."
            )
            self.start_button.setText("Transcribe again")
        else:
            self.previous_status.setText(job.error or "Transcription was cancelled.")
            self.start_button.setText("Retry offline transcription")

    def set_cancelling(self) -> None:
        self.cancel_button.setEnabled(False)
        self.progress_detail.setText(
            "Cancellation requested; stopping at the current download or model boundary…"
        )

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
            f"Larger profiles need more memory and processing time. Cache: "
            f"{default_models_directory()}."
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

    def _update_diarization_controls(self, *_args: object) -> None:
        enabled = self.separate_remote_speakers_checkbox.isChecked()
        self.diarization_description.setEnabled(enabled)
        self.min_remote_speakers.setEnabled(enabled)
        self.max_remote_speakers.setEnabled(enabled)
        self.allow_diarization_download_checkbox.setEnabled(enabled)
        self.diarization_token_input.setEnabled(
            enabled and self.allow_diarization_download_checkbox.isChecked()
        )

    def _emit_start(self) -> None:
        profile = self.profile_combo.currentData()
        if self._session_id is None or not isinstance(profile, str):
            return
        separate_remote_speakers = self.separate_remote_speakers_checkbox.isChecked()
        min_remote_speakers = self.min_remote_speakers.value() if separate_remote_speakers else 0
        max_remote_speakers = self.max_remote_speakers.value() if separate_remote_speakers else 0
        if (
            min_remote_speakers
            and max_remote_speakers
            and min_remote_speakers > max_remote_speakers
        ):
            self.previous_status.setText(
                "Minimum remote speakers cannot be greater than the maximum."
            )
            return
        allow_diarization_download = (
            separate_remote_speakers and self.allow_diarization_download_checkbox.isChecked()
        )
        access_token = (
            self.diarization_token_input.text().strip() if allow_diarization_download else ""
        )
        self.start_requested.emit(
            self._session_id,
            profile,
            self._language(),
            self.hotwords_input.text().strip(),
            self.allow_download_checkbox.isChecked(),
            separate_remote_speakers,
            min_remote_speakers,
            max_remote_speakers,
            allow_diarization_download,
            access_token,
        )
        self.diarization_token_input.clear()

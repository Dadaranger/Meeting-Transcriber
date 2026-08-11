from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from meeting_transcriber.capture.devices import AudioDevice, AudioDeviceCatalog
from meeting_transcriber.domain.session import CONSENT_STATEMENT, MeetingSession


def _label(text: str, object_name: str | None = None, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if object_name is not None:
        label.setObjectName(object_name)
    label.setWordWrap(wrap)
    return label


class RecordingPage(QWidget):
    """Review capture sources and require an explicit consent acknowledgement."""

    begin_requested = Signal(str, str, str)
    back_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._session_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(38, 32, 38, 32)
        root.setSpacing(16)
        root.addWidget(_label("RECORDING SETUP", "eyebrow"))
        root.addWidget(_label("Review before recording", "pageTitle"))
        root.addWidget(
            _label(
                "No audio stream is opened on this screen. Confirm the meeting, devices, "
                "and participant notice before recording begins.",
                "muted",
                wrap=True,
            )
        )

        card = QFrame()
        card.setObjectName("recordingCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 22, 24, 24)
        card_layout.setSpacing(12)

        card_layout.addWidget(_label("Meeting", "muted"))
        self.meeting_title_label = _label("No meeting selected", "sectionTitle", wrap=True)
        card_layout.addWidget(self.meeting_title_label)

        card_layout.addSpacing(4)
        card_layout.addWidget(_label("Microphone", "muted"))
        self.microphone_combo = QComboBox()
        self.microphone_combo.setAccessibleName("Recording microphone")
        self.microphone_combo.currentIndexChanged.connect(self._update_begin_enabled)
        card_layout.addWidget(self.microphone_combo)

        card_layout.addWidget(_label("Meeting/system audio", "muted"))
        self.loopback_combo = QComboBox()
        self.loopback_combo.setAccessibleName("Meeting system audio")
        self.loopback_combo.currentIndexChanged.connect(self._update_begin_enabled)
        card_layout.addWidget(self.loopback_combo)

        self.device_status_label = _label("Audio devices have not been loaded.", "muted", wrap=True)
        card_layout.addWidget(self.device_status_label)

        card_layout.addSpacing(6)
        self.consent_checkbox = QCheckBox(CONSENT_STATEMENT)
        self.consent_checkbox.setObjectName("consentCheckbox")
        self.consent_checkbox.setAccessibleName("Confirm participant recording consent")
        self.consent_checkbox.toggled.connect(self._update_begin_enabled)
        card_layout.addWidget(self.consent_checkbox)
        card_layout.addWidget(
            _label(
                "This acknowledgement records your confirmation; it does not independently "
                "verify participant consent or guarantee legal compliance.",
                "muted",
                wrap=True,
            )
        )

        actions = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.back_requested.emit)
        actions.addWidget(self.back_button)
        actions.addStretch()
        self.begin_button = QPushButton("Begin recording")
        self.begin_button.setObjectName("primaryButton")
        self.begin_button.setAccessibleName("Begin recording after consent confirmation")
        self.begin_button.setEnabled(False)
        self.begin_button.clicked.connect(self._emit_begin_requested)
        actions.addWidget(self.begin_button)
        card_layout.addLayout(actions)

        root.addWidget(card)
        root.addStretch()

    def load_session(self, session: MeetingSession, catalog: AudioDeviceCatalog) -> None:
        self._session_id = session.session_id
        self.meeting_title_label.setText(session.title)
        self.consent_checkbox.setChecked(False)
        self._populate_devices(self.microphone_combo, catalog.microphones)
        self._populate_devices(self.loopback_combo, catalog.loopbacks)

        if catalog.microphones and catalog.loopbacks:
            self.device_status_label.setText(
                "Both sources are available. Device discovery does not record audio."
            )
        else:
            missing = []
            if not catalog.microphones:
                missing.append("microphone")
            if not catalog.loopbacks:
                missing.append("system-audio loopback")
            self.device_status_label.setText(f"Missing capture source: {', '.join(missing)}.")
        self._update_begin_enabled()

    def show_device_error(self, message: str) -> None:
        self.microphone_combo.clear()
        self.loopback_combo.clear()
        self.device_status_label.setText(f"Audio device discovery failed: {message}")
        self._update_begin_enabled()

    @staticmethod
    def _populate_devices(combo: QComboBox, devices: tuple[AudioDevice, ...]) -> None:
        combo.clear()
        for device in devices:
            channels = "channel" if device.max_input_channels == 1 else "channels"
            default = " — default" if device.is_default else ""
            combo.addItem(
                f"{device.name} — {device.default_sample_rate} Hz, "
                f"{device.max_input_channels} {channels}{default}",
                device.device_id,
            )
        default_index = next(
            (index for index, device in enumerate(devices) if device.is_default),
            0,
        )
        if devices:
            combo.setCurrentIndex(default_index)

    def _update_begin_enabled(self, *_args: object) -> None:
        has_microphone = isinstance(self.microphone_combo.currentData(), str)
        has_loopback = isinstance(self.loopback_combo.currentData(), str)
        self.begin_button.setEnabled(
            self._session_id is not None
            and has_microphone
            and has_loopback
            and self.consent_checkbox.isChecked()
        )

    def _emit_begin_requested(self) -> None:
        microphone_id = self.microphone_combo.currentData()
        loopback_id = self.loopback_combo.currentData()
        if (
            self._session_id is None
            or not isinstance(microphone_id, str)
            or not isinstance(loopback_id, str)
            or not self.consent_checkbox.isChecked()
        ):
            return
        self.begin_requested.emit(self._session_id, microphone_id, loopback_id)

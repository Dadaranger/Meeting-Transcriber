from __future__ import annotations

import time

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from meeting_transcriber.app.storage_health import DiskSpaceStatus
from meeting_transcriber.capture.devices import AudioDevice, AudioDeviceCatalog
from meeting_transcriber.domain.session import CONSENT_STATEMENT, MeetingSession
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


class RecordingPage(QWidget):
    """Review capture sources and require an explicit consent acknowledgement."""

    begin_requested = Signal(str, str, str)
    preflight_requested = Signal(str, str, str)
    preflight_stop_requested = Signal()
    back_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._session_id: str | None = None
        self._preflight_active = False

        root, self.scroll_area = create_scrollable_page(
            self,
            accessible_name="Recording setup and status",
            margins=(38, 32, 38, 32),
            spacing=16,
        )
        root.addWidget(_label("RECORDING SETUP", "eyebrow"))
        root.addWidget(_label("Review before recording", "pageTitle"))
        root.addWidget(
            _label(
                "Confirm the meeting, devices, and participant notice before testing sources "
                "or recording. The source test never saves audio.",
                "muted",
                wrap=True,
            )
        )

        self.setup_card = QFrame()
        self.setup_card.setObjectName("recordingCard")
        card_layout = QVBoxLayout(self.setup_card)
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
        self.storage_status_label = _label(
            "Meeting storage has not been checked.", "muted", wrap=True
        )
        self.storage_status_label.setAccessibleName("Available meeting storage")
        card_layout.addWidget(self.storage_status_label)

        card_layout.addSpacing(6)
        self.consent_checkbox = QCheckBox(
            CONSENT_STATEMENT.replace("been informed and", "been informed\nand")
        )
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

        self.preflight_status_label = _label(
            "Optional: test both source levels for five seconds before recording.",
            "muted",
            wrap=True,
        )
        card_layout.addWidget(self.preflight_status_label)
        self.preflight_microphone_level = QProgressBar()
        self.preflight_microphone_level.setRange(0, 100)
        self.preflight_microphone_level.setValue(0)
        self.preflight_microphone_level.setTextVisible(False)
        self.preflight_microphone_level.setAccessibleName("Source test microphone level")
        card_layout.addWidget(self.preflight_microphone_level)
        self.preflight_system_level = QProgressBar()
        self.preflight_system_level.setRange(0, 100)
        self.preflight_system_level.setValue(0)
        self.preflight_system_level.setTextVisible(False)
        self.preflight_system_level.setAccessibleName("Source test system audio level")
        card_layout.addWidget(self.preflight_system_level)

        actions = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.back_requested.emit)
        actions.addWidget(self.back_button)
        self.preflight_button = QPushButton("Test sources (5 seconds)")
        self.preflight_button.setAccessibleName(
            "Test selected audio sources after consent confirmation"
        )
        self.preflight_button.setEnabled(False)
        self.preflight_button.clicked.connect(self._emit_preflight_toggle)
        actions.addWidget(self.preflight_button)
        actions.addStretch()
        self.begin_button = QPushButton("Begin recording")
        self.begin_button.setObjectName("primaryButton")
        self.begin_button.setAccessibleName("Begin recording after consent confirmation")
        self.begin_button.setEnabled(False)
        self.begin_button.clicked.connect(self._emit_begin_requested)
        actions.addWidget(self.begin_button)
        card_layout.addLayout(actions)

        root.addWidget(self.setup_card)

        self.recording_card = QFrame()
        self.recording_card.setObjectName("recordingCard")
        recording_layout = QVBoxLayout(self.recording_card)
        recording_layout.setContentsMargins(24, 22, 24, 24)
        recording_layout.setSpacing(14)

        live_header = QHBoxLayout()
        self.recording_pill = _label("● RECORDING", "recordingPill")
        live_header.addWidget(self.recording_pill)
        live_header.addStretch()
        self.elapsed_label = _label("00:00:00", "pageTitle")
        self.elapsed_label.setAccessibleName("Recording elapsed time")
        live_header.addWidget(self.elapsed_label)
        recording_layout.addLayout(live_header)

        self.live_meeting_title = _label("Meeting", "sectionTitle", wrap=True)
        recording_layout.addWidget(self.live_meeting_title)
        recording_layout.addWidget(
            _label(
                "Recording is active. Keep participants informed; use Stop recording to "
                "finalize both recoverable audio sources.",
                "muted",
                wrap=True,
            )
        )
        self.live_sources_label = _label("", "muted", wrap=True)
        recording_layout.addWidget(self.live_sources_label)
        self.live_storage_label = _label("", "muted", wrap=True)
        self.live_storage_label.setAccessibleName("Live available meeting storage")
        recording_layout.addWidget(self.live_storage_label)

        recording_layout.addWidget(_label("Microphone level", "muted"))
        self.microphone_level = QProgressBar()
        self.microphone_level.setRange(0, 100)
        self.microphone_level.setValue(0)
        self.microphone_level.setTextVisible(False)
        self.microphone_level.setAccessibleName("Live microphone input level")
        recording_layout.addWidget(self.microphone_level)

        recording_layout.addWidget(_label("System audio level", "muted"))
        self.system_audio_level = QProgressBar()
        self.system_audio_level.setRange(0, 100)
        self.system_audio_level.setValue(0)
        self.system_audio_level.setTextVisible(False)
        self.system_audio_level.setAccessibleName("Live system audio input level")
        recording_layout.addWidget(self.system_audio_level)

        live_actions = QHBoxLayout()
        live_actions.addStretch()
        self.pause_button = QPushButton("Pause recording")
        self.pause_button.setAccessibleName("Pause recording")
        self.pause_button.clicked.connect(self._emit_pause_toggle)
        live_actions.addWidget(self.pause_button)
        self.stop_button = QPushButton("Stop recording")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setAccessibleName("Stop and finalize recording")
        self.stop_button.clicked.connect(self.stop_requested.emit)
        live_actions.addWidget(self.stop_button)
        recording_layout.addLayout(live_actions)
        self.recording_card.hide()
        root.addWidget(self.recording_card)
        root.addStretch()

        self._recording_started_monotonic: float | None = None
        self._pause_started_monotonic: float | None = None
        self._paused_duration_seconds = 0.0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1_000)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

    def load_session(self, session: MeetingSession, catalog: AudioDeviceCatalog) -> None:
        self._elapsed_timer.stop()
        self._recording_started_monotonic = None
        self._pause_started_monotonic = None
        self._paused_duration_seconds = 0.0
        self._preflight_active = False
        self.recording_card.hide()
        self.setup_card.show()
        self._session_id = session.session_id
        self.meeting_title_label.setText(session.title)
        self.consent_checkbox.setChecked(False)
        self.show_preflight(False)
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
        reset_scroll_position(self.scroll_area)

    def show_recording(self, session: MeetingSession) -> None:
        self.live_meeting_title.setText(session.title)
        self.live_sources_label.setText(
            f"Microphone: {self.microphone_combo.currentText()}\n"
            f"System audio: {self.loopback_combo.currentText()}"
        )
        self.setup_card.hide()
        self.recording_card.show()
        self.update_levels(0.0, 0.0)
        self._recording_started_monotonic = time.monotonic()
        self._pause_started_monotonic = None
        self._paused_duration_seconds = 0.0
        self.recording_pill.setText("● RECORDING")
        self.pause_button.setText("Pause recording")
        self.pause_button.setAccessibleName("Pause recording")
        self._update_elapsed()
        self._elapsed_timer.start()
        reset_scroll_position(self.scroll_area)

    def recording_finished(self) -> None:
        self._elapsed_timer.stop()
        self._recording_started_monotonic = None
        self._pause_started_monotonic = None
        self._paused_duration_seconds = 0.0
        self.recording_card.hide()

    def update_levels(self, microphone: float, system_audio: float) -> None:
        microphone_percent = round(max(0.0, min(1.0, microphone)) * 100)
        system_percent = round(max(0.0, min(1.0, system_audio)) * 100)
        self.microphone_level.setValue(microphone_percent)
        self.system_audio_level.setValue(system_percent)
        self.preflight_microphone_level.setValue(microphone_percent)
        self.preflight_system_level.setValue(system_percent)

    def update_storage(self, status: DiskSpaceStatus) -> None:
        self.storage_status_label.setText(status.display_text)
        self.live_storage_label.setText(status.display_text)

    def show_storage_error(self, message: str) -> None:
        text = f"Meeting storage check unavailable: {message}"
        self.storage_status_label.setText(text)
        self.live_storage_label.setText(text)

    def show_preflight(self, active: bool) -> None:
        self._preflight_active = active
        self.preflight_button.setText("Stop source test" if active else "Test sources (5 seconds)")
        self.preflight_status_label.setText(
            "Testing now — speak into the microphone and play meeting audio. Nothing is saved."
            if active
            else "Optional: test both source levels for five seconds before recording."
        )
        self.microphone_combo.setEnabled(not active)
        self.loopback_combo.setEnabled(not active)
        self.consent_checkbox.setEnabled(not active)
        self.back_button.setEnabled(not active)
        if not active:
            self.update_levels(0.0, 0.0)
        self._update_begin_enabled()

    def show_paused(self) -> None:
        if self._pause_started_monotonic is None:
            self._pause_started_monotonic = time.monotonic()
        self.recording_pill.setText("Ⅱ PAUSED")
        self.pause_button.setText("Resume recording")
        self.pause_button.setAccessibleName("Resume recording")
        self.update_levels(0.0, 0.0)
        self._update_elapsed()

    def show_resumed(self) -> None:
        if self._pause_started_monotonic is not None:
            self._paused_duration_seconds += time.monotonic() - self._pause_started_monotonic
        self._pause_started_monotonic = None
        self.recording_pill.setText("● RECORDING")
        self.pause_button.setText("Pause recording")
        self.pause_button.setAccessibleName("Pause recording")

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
        ready = (
            self._session_id is not None
            and has_microphone
            and has_loopback
            and self.consent_checkbox.isChecked()
        )
        self.begin_button.setEnabled(ready and not self._preflight_active)
        self.preflight_button.setEnabled(ready or self._preflight_active)

    def _emit_begin_requested(self) -> None:
        microphone_id = self.microphone_combo.currentData()
        loopback_id = self.loopback_combo.currentData()
        if (
            self._session_id is None
            or self._preflight_active
            or not isinstance(microphone_id, str)
            or not isinstance(loopback_id, str)
            or not self.consent_checkbox.isChecked()
        ):
            return
        self.begin_requested.emit(self._session_id, microphone_id, loopback_id)

    def _emit_preflight_toggle(self) -> None:
        if self._preflight_active:
            self.preflight_stop_requested.emit()
            return
        microphone_id = self.microphone_combo.currentData()
        loopback_id = self.loopback_combo.currentData()
        if (
            self._session_id is None
            or not isinstance(microphone_id, str)
            or not isinstance(loopback_id, str)
            or not self.consent_checkbox.isChecked()
        ):
            return
        self.preflight_requested.emit(self._session_id, microphone_id, loopback_id)

    def _emit_pause_toggle(self) -> None:
        if self._pause_started_monotonic is None:
            self.pause_requested.emit()
        else:
            self.resume_requested.emit()

    def _update_elapsed(self) -> None:
        if self._recording_started_monotonic is None:
            elapsed_seconds = 0
        else:
            now = time.monotonic()
            active_pause = (
                now - self._pause_started_monotonic
                if self._pause_started_monotonic is not None
                else 0.0
            )
            elapsed_seconds = max(
                0,
                int(
                    now
                    - self._recording_started_monotonic
                    - self._paused_duration_seconds
                    - active_pause
                ),
            )
        hours, remainder = divmod(elapsed_seconds, 3_600)
        minutes, seconds = divmod(remainder, 60)
        self.elapsed_label.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")

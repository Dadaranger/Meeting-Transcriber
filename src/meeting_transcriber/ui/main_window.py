from __future__ import annotations

import platform
import sys

from PySide6.QtCore import Qt, Signal, qVersion
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from meeting_transcriber.app.recording_service import (
    MeetingRecordingService,
    RecordingWorkflow,
    RecordingWorkflowError,
)
from meeting_transcriber.app.session_service import MeetingSessionService
from meeting_transcriber.capture.devices import (
    AudioDeviceCatalog,
    AudioDeviceDiscovery,
    DeviceDiscoveryError,
)
from meeting_transcriber.capture.windows_pyaudio import (
    PyAudioWPatchDeviceBackend,
    PyAudioWPatchStreamFactory,
)
from meeting_transcriber.domain.session import SessionState
from meeting_transcriber.infrastructure.paths import default_meetings_directory
from meeting_transcriber.storage.session_store import SessionStore
from meeting_transcriber.ui.recording_page import RecordingPage

APP_STYLE = """
QWidget {
    background-color: #0b1120;
    color: #e8eef8;
    font-family: "Segoe UI";
    font-size: 14px;
}
QFrame#sidebar {
    background-color: #101827;
    border-right: 1px solid #243148;
}
QFrame#hero, QFrame#featureCard, QFrame#diagnosticCard, QFrame#recordingCard {
    background-color: #131e31;
    border: 1px solid #263550;
    border-radius: 14px;
}
QLabel#brandMark {
    background-color: #5eead4;
    color: #0b1120;
    border-radius: 10px;
    font-weight: 800;
    font-size: 16px;
}
QLabel#eyebrow {
    color: #5eead4;
    font-size: 12px;
    font-weight: 700;
}
QLabel#pageTitle {
    color: #f8fafc;
    font-size: 30px;
    font-weight: 700;
}
QLabel#sectionTitle {
    color: #f8fafc;
    font-size: 19px;
    font-weight: 650;
}
QLabel#muted {
    color: #9aabc2;
}
QLabel#statusPill {
    background-color: #153c39;
    color: #8cf5e5;
    border: 1px solid #24665e;
    border-radius: 10px;
    padding: 5px 10px;
    font-size: 12px;
    font-weight: 650;
}
QLabel#recordingPill {
    background-color: #4a1620;
    color: #ff9caa;
    border: 1px solid #913344;
    border-radius: 10px;
    padding: 6px 11px;
    font-size: 12px;
    font-weight: 750;
}
QPushButton {
    border: 1px solid #30415f;
    border-radius: 9px;
    padding: 10px 15px;
    text-align: left;
    background-color: #17243a;
}
QPushButton:hover {
    background-color: #1e304d;
    border-color: #47658f;
}
QPushButton:checked {
    background-color: #173c42;
    border-color: #3ba99b;
    color: #9af8e9;
}
QPushButton#primaryButton {
    background-color: #5eead4;
    color: #0b1120;
    border: none;
    font-weight: 750;
    text-align: center;
    padding: 12px 18px;
}
QPushButton#primaryButton:hover {
    background-color: #7cf3e0;
}
QPushButton#primaryButton:disabled {
    background-color: #263550;
    color: #718198;
}
QPushButton#dangerButton {
    background-color: #d83a52;
    color: #ffffff;
    border: none;
    font-weight: 750;
    text-align: center;
    padding: 12px 18px;
}
QPushButton#dangerButton:hover {
    background-color: #ec4c63;
}
QComboBox {
    background-color: #17243a;
    border: 1px solid #30415f;
    border-radius: 8px;
    padding: 9px 11px;
}
QCheckBox#consentCheckbox {
    color: #e8eef8;
    spacing: 10px;
    padding: 8px 0;
}
QStatusBar {
    background-color: #101827;
    color: #8fa0b8;
    border-top: 1px solid #243148;
}
"""


def _label(text: str, object_name: str | None = None, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if object_name is not None:
        label.setObjectName(object_name)
    label.setWordWrap(wrap)
    return label


class FeatureCard(QFrame):
    def __init__(self, title: str, description: str, accent: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("featureCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 17, 18, 17)
        layout.setSpacing(8)

        accent_label = _label(accent, "eyebrow")
        title_label = _label(title, "sectionTitle")
        description_label = _label(description, "muted", wrap=True)
        layout.addWidget(accent_label)
        layout.addWidget(title_label)
        layout.addWidget(description_label)


class HomePage(QWidget):
    draft_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(38, 32, 38, 32)
        root.setSpacing(20)

        status_row = QHBoxLayout()
        status_row.addWidget(_label("LOCAL-FIRST DESKTOP APP", "eyebrow"))
        status_row.addStretch()
        status = _label("Foundation ready", "statusPill")
        status.setFixedHeight(30)
        status_row.addWidget(status)
        root.addLayout(status_row)

        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(30, 28, 30, 30)
        hero_layout.setSpacing(13)

        hero_layout.addWidget(_label("Turn conversations into clear meeting notes", "pageTitle"))
        hero_layout.addWidget(
            _label(
                "Record microphone and meeting audio, process it locally, review speakers, "
                "and export a durable Markdown record.",
                "muted",
                wrap=True,
            )
        )

        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        self.start_button = QPushButton("Create a meeting draft")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setAccessibleName("Create a meeting draft")
        self.start_button.clicked.connect(self.draft_requested.emit)
        action_row.addWidget(self.start_button)
        action_row.addStretch()
        hero_layout.addSpacing(6)
        hero_layout.addLayout(action_row)
        root.addWidget(hero)

        root.addWidget(_label("Designed around trustworthy local processing", "sectionTitle"))

        cards = QHBoxLayout()
        cards.setSpacing(14)
        cards.addWidget(
            FeatureCard(
                "Two-source capture",
                "Microphone and Windows system audio remain separate and recoverable.",
                "RECORD",
            )
        )
        cards.addWidget(
            FeatureCard(
                "Offline transcript",
                "Speech processing is designed to work without uploading the meeting.",
                "TRANSCRIBE",
            )
        )
        cards.addWidget(
            FeatureCard(
                "Readable export",
                "Review speaker labels, then produce portable Markdown and JSON files.",
                "REVIEW",
            )
        )
        root.addLayout(cards)
        root.addStretch()


class DiagnosticCard(QFrame):
    def __init__(self, name: str, value: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("diagnosticCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(5)
        layout.addWidget(_label(name, "muted"))
        self.value_label = _label(value, "sectionTitle", wrap=True)
        self.value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.value_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class DiagnosticsPage(QWidget):
    def __init__(
        self,
        audio_backend: AudioDeviceDiscovery | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.audio_backend = audio_backend or PyAudioWPatchDeviceBackend()
        root = QVBoxLayout(self)
        root.setContentsMargins(38, 32, 38, 32)
        root.setSpacing(15)

        root.addWidget(_label("DIAGNOSTICS", "eyebrow"))
        root.addWidget(_label("Runtime information", "pageTitle"))
        root.addWidget(
            _label(
                "These details will support hardware checks, model selection, and support bundles.",
                "muted",
                wrap=True,
            )
        )
        root.addSpacing(6)
        root.addWidget(DiagnosticCard("Operating system", platform.platform()))
        root.addWidget(DiagnosticCard("Python", sys.version.split()[0]))
        root.addWidget(DiagnosticCard("Qt", qVersion()))
        root.addWidget(
            DiagnosticCard(
                "Default meeting folder",
                str(default_meetings_directory()),
            )
        )
        audio_row = QHBoxLayout()
        self.audio_card = DiagnosticCard(
            "Windows capture devices",
            "Not checked - refresh to enumerate devices without recording.",
        )
        audio_row.addWidget(self.audio_card, 1)
        self.refresh_audio_button = QPushButton("Refresh audio devices")
        self.refresh_audio_button.setAccessibleName("Refresh Windows audio devices")
        self.refresh_audio_button.clicked.connect(self._refresh_audio_devices)
        audio_row.addWidget(self.refresh_audio_button)
        root.addLayout(audio_row)
        root.addStretch()

    def _refresh_audio_devices(self) -> None:
        try:
            catalog = self.audio_backend.discover_devices()
        except DeviceDiscoveryError as error:
            self.audio_card.set_value(f"Audio discovery failed: {error}")
            return

        microphones = ", ".join(device.name for device in catalog.microphones) or "None"
        loopbacks = ", ".join(device.name for device in catalog.loopbacks) or "None"
        self.audio_card.set_value(f"Microphones: {microphones}\nSystem loopbacks: {loopbacks}")


class MainWindow(QMainWindow):
    def __init__(
        self,
        session_service: MeetingSessionService | None = None,
        audio_backend: AudioDeviceDiscovery | None = None,
        recording_service: RecordingWorkflow | None = None,
    ):
        super().__init__()
        self.session_service = session_service or MeetingSessionService(
            SessionStore(default_meetings_directory())
        )
        self.audio_backend = audio_backend or PyAudioWPatchDeviceBackend()
        self.recording_service = recording_service or MeetingRecordingService(
            self.session_service,
            self.audio_backend,
            PyAudioWPatchStreamFactory(),
        )
        self.setWindowTitle("Meeting Transcriber")
        self.setMinimumSize(960, 640)
        self.resize(1120, 720)

        shell = QWidget()
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        sidebar = self._build_sidebar()
        self.pages = QStackedWidget()
        self.home_page = HomePage()
        self.home_page.draft_requested.connect(self._create_draft)
        self.pages.addWidget(self.home_page)
        self.recording_page = RecordingPage()
        self.recording_page.begin_requested.connect(self._begin_recording)
        self.recording_page.stop_requested.connect(self._stop_recording)
        self.recording_page.back_requested.connect(self._show_home)
        self.pages.addWidget(self.recording_page)
        self.diagnostics_page = DiagnosticsPage(self.audio_backend)
        self.pages.addWidget(self.diagnostics_page)

        shell_layout.addWidget(sidebar)
        shell_layout.addWidget(self.pages, 1)
        self.setCentralWidget(shell)

        status = QStatusBar()
        status.showMessage("Ready - local processing by default")
        self.global_recording_indicator = _label("● RECORDING", "recordingPill")
        self.global_recording_indicator.hide()
        status.addPermanentWidget(self.global_recording_indicator)
        self.setStatusBar(status)

    def _create_draft(self) -> None:
        title, accepted = QInputDialog.getText(
            self,
            "Create meeting draft",
            "Meeting title:",
            text="Untitled meeting",
        )
        if not accepted:
            return
        session = self.session_service.create_draft(title)
        try:
            catalog = self.recording_service.discover_devices()
        except DeviceDiscoveryError as error:
            catalog = AudioDeviceCatalog((), ())
            self.recording_page.load_session(session, catalog)
            self.recording_page.show_device_error(str(error))
        else:
            self.recording_page.load_session(session, catalog)
        self.pages.setCurrentWidget(self.recording_page)
        self.statusBar().showMessage(f"Draft saved - review recording setup for {session.title}")

    def _begin_recording(
        self,
        session_id: str,
        microphone_id: str,
        loopback_id: str,
    ) -> None:
        try:
            session = self.recording_service.start(
                session_id,
                microphone_id,
                loopback_id,
                consent_confirmed=self.recording_page.consent_checkbox.isChecked(),
            )
        except RecordingWorkflowError as error:
            self.statusBar().showMessage(f"Recording did not start - {error}")
            QMessageBox.critical(self, "Recording could not start", str(error))
            return

        self.recording_page.show_recording(session)
        self.global_recording_indicator.show()
        self._set_navigation_enabled(False)
        self.statusBar().showMessage(f"Recording - {session.title}")

    def _stop_recording(self) -> None:
        try:
            result = self.recording_service.stop()
        except RecordingWorkflowError as error:
            self.statusBar().showMessage(f"Recording interrupted - {error}")
            QMessageBox.critical(self, "Recording was interrupted", str(error))
        else:
            if result.session.state is SessionState.INTERRUPTED:
                self.statusBar().showMessage(
                    f"Recording interrupted - recoverable audio retained for {result.session.title}"
                )
                QMessageBox.warning(
                    self,
                    "Recording interrupted",
                    "The capture reported an interruption. Recoverable audio and diagnostics "
                    "were retained locally.",
                )
            else:
                self.statusBar().showMessage(
                    f"Recording saved locally - {result.session.title}",
                    10_000,
                )
                QMessageBox.information(
                    self,
                    "Recording saved",
                    "Microphone and system-audio chunks were finalized locally.",
                )
        finally:
            self.recording_page.recording_finished()
            self.global_recording_indicator.hide()
            self._set_navigation_enabled(True)
            self.pages.setCurrentWidget(self.home_page)

    def _show_home(self) -> None:
        if not self.recording_service.is_recording:
            self.pages.setCurrentWidget(self.home_page)
            self.home_button.setChecked(True)

    def _set_navigation_enabled(self, enabled: bool) -> None:
        self.home_button.setEnabled(enabled)
        self.diagnostics_button.setEnabled(enabled)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.recording_service.is_recording:
            super().closeEvent(event)
            return

        answer = QMessageBox.question(
            self,
            "Stop recording and close?",
            "The meeting is still recording. Stop and finalize both audio sources before closing?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is QMessageBox.StandardButton.No:
            event.ignore()
            return
        self._stop_recording()
        event.accept()

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(230)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 22, 18, 20)
        layout.setSpacing(10)

        brand_row = QHBoxLayout()
        brand = _label("MT", "brandMark")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setFixedSize(42, 42)
        brand_row.addWidget(brand)
        brand_text = QVBoxLayout()
        product = _label("Meeting Transcriber", "sectionTitle")
        product.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        brand_text.addWidget(product)
        brand_text.addWidget(_label("Private meeting notes", "muted"))
        brand_row.addLayout(brand_text)
        layout.addLayout(brand_row)
        layout.addSpacing(22)

        self.home_button = QPushButton("Home")
        self.diagnostics_button = QPushButton("Diagnostics")
        for button in (self.home_button, self.diagnostics_button):
            button.setCheckable(True)
            button.setAutoExclusive(True)
            layout.addWidget(button)
        self.home_button.setChecked(True)
        self.home_button.clicked.connect(lambda: self.pages.setCurrentWidget(self.home_page))
        self.diagnostics_button.clicked.connect(
            lambda: self.pages.setCurrentWidget(self.diagnostics_page)
        )

        layout.addStretch()
        privacy = _label(
            "Audio stays on this computer unless you explicitly choose otherwise.",
            "muted",
            wrap=True,
        )
        layout.addWidget(privacy)
        return sidebar

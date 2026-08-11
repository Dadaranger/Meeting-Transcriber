from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from meeting_transcriber.capture.devices import (
    AudioDevice,
    AudioDeviceCatalog,
    AudioDeviceKind,
)
from meeting_transcriber.domain.session import CONSENT_STATEMENT, MeetingSession, SessionState
from meeting_transcriber.ui.recording_page import RecordingPage


def _device(
    kind: AudioDeviceKind,
    device_id: str,
    name: str,
    *,
    is_default: bool = False,
) -> AudioDevice:
    return AudioDevice(
        device_id=device_id,
        backend_index=1,
        name=name,
        kind=kind,
        host_api="Test",
        max_input_channels=1 if kind is AudioDeviceKind.MICROPHONE else 2,
        default_sample_rate=48_000,
        is_default=is_default,
    )


def _catalog() -> AudioDeviceCatalog:
    return AudioDeviceCatalog(
        microphones=(
            _device(AudioDeviceKind.MICROPHONE, "mic-1", "Desk microphone"),
            _device(
                AudioDeviceKind.MICROPHONE,
                "mic-2",
                "Headset microphone",
                is_default=True,
            ),
        ),
        loopbacks=(
            _device(
                AudioDeviceKind.SYSTEM_LOOPBACK,
                "loopback-1",
                "Speakers [Loopback]",
                is_default=True,
            ),
        ),
    )


def test_consent_is_a_hard_gate_for_begin_recording(qtbot: QtBot) -> None:
    page = RecordingPage()
    qtbot.addWidget(page)
    session = MeetingSession.new("Weekly sync")

    page.load_session(session, _catalog())

    assert not page.begin_button.isEnabled()
    assert page.consent_checkbox.text() == CONSENT_STATEMENT
    assert page.microphone_combo.currentData() == "mic-2"
    assert page.loopback_combo.currentData() == "loopback-1"

    qtbot.mouseClick(page.consent_checkbox, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]

    assert page.begin_button.isEnabled()
    with qtbot.waitSignal(page.begin_requested) as signal:
        qtbot.mouseClick(page.begin_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]

    assert signal.args == [session.session_id, "mic-2", "loopback-1"]


def test_missing_source_keeps_begin_recording_disabled(qtbot: QtBot) -> None:
    page = RecordingPage()
    qtbot.addWidget(page)
    session = MeetingSession.new("Weekly sync")
    catalog = AudioDeviceCatalog(
        microphones=(_device(AudioDeviceKind.MICROPHONE, "mic-1", "Desk microphone"),),
        loopbacks=(),
    )

    page.load_session(session, catalog)
    qtbot.mouseClick(page.consent_checkbox, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]

    assert not page.begin_button.isEnabled()
    assert "system-audio loopback" in page.device_status_label.text()


def test_device_error_clears_previous_choices(qtbot: QtBot) -> None:
    page = RecordingPage()
    qtbot.addWidget(page)
    page.load_session(MeetingSession.new("Weekly sync"), _catalog())

    page.show_device_error("WASAPI unavailable")

    assert page.microphone_combo.count() == 0
    assert page.loopback_combo.count() == 0
    assert not page.begin_button.isEnabled()
    assert "WASAPI unavailable" in page.device_status_label.text()


def test_recording_state_has_persistent_timer_sources_and_stop_control(qtbot: QtBot) -> None:
    page = RecordingPage()
    qtbot.addWidget(page)
    draft = MeetingSession.new("Weekly sync")
    recording = draft.confirm_consent().transition(SessionState.RECORDING)
    page.load_session(draft, _catalog())

    page.show_recording(recording)

    assert page.setup_card.isHidden()
    assert not page.recording_card.isHidden()
    assert page.recording_pill.text() == "● RECORDING"
    assert page.elapsed_label.text() == "00:00:00"
    assert "Headset microphone" in page.live_sources_label.text()
    assert "Speakers [Loopback]" in page.live_sources_label.text()

    page.update_levels(0.42, 0.91)
    assert page.microphone_level.value() == 42
    assert page.system_audio_level.value() == 91

    with qtbot.waitSignal(page.stop_requested):
        qtbot.mouseClick(page.stop_button, Qt.MouseButton.LeftButton)  # type: ignore[no-untyped-call]

    page.recording_finished()
    assert page.recording_card.isHidden()

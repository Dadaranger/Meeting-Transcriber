from datetime import UTC, datetime, timedelta

import pytest

from meeting_transcriber.domain.session import (
    CONSENT_STATEMENT_VERSION,
    REQUIRED_CONSENT_SOURCES,
    ConsentCaptureSource,
    InvalidSessionTransition,
    MeetingSession,
    SessionOrigin,
    SessionState,
)

SESSION_ID = "bfb5fe95-053d-46f4-b369-d6f902bd70db"
START = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def test_new_session_is_a_normalized_draft() -> None:
    session = MeetingSession.new("  Weekly sync  ", session_id=SESSION_ID, now=START)

    assert session.title == "Weekly sync"
    assert session.state is SessionState.DRAFT
    assert session.created_at == START
    assert session.updated_at == START
    assert session.revision == 0
    assert session.origin is SessionOrigin.LIVE_RECORDING
    assert session.consent_confirmed_at is None
    assert not session.has_current_recording_consent


def test_imported_session_starts_recorded_without_capture_consent() -> None:
    session = MeetingSession.imported("  Interview video  ", session_id=SESSION_ID, now=START)

    assert session.title == "Interview video"
    assert session.origin is SessionOrigin.IMPORTED_MEDIA
    assert session.state is SessionState.RECORDED
    assert session.started_at == START
    assert session.stopped_at == START
    assert session.consent_confirmed_at is None
    assert not session.has_current_recording_consent


def test_recording_requires_confirmed_consent() -> None:
    session = MeetingSession.new(session_id=SESSION_ID, now=START)

    with pytest.raises(InvalidSessionTransition, match="Consent"):
        session.transition(SessionState.RECORDING, at=START + timedelta(seconds=1))


def test_recording_lifecycle_tracks_timestamps_and_revisions() -> None:
    consent_time = START + timedelta(seconds=1)
    record_time = START + timedelta(seconds=2)
    stop_time = START + timedelta(minutes=5)

    session = MeetingSession.new(session_id=SESSION_ID, now=START)
    session = session.confirm_consent(at=consent_time)
    session = session.transition(SessionState.RECORDING, at=record_time)
    session = session.transition(SessionState.PAUSED, at=record_time + timedelta(minutes=1))
    session = session.transition(SessionState.RECORDING, at=record_time + timedelta(minutes=2))
    session = session.transition(SessionState.RECORDED, at=stop_time)

    assert session.state is SessionState.RECORDED
    assert session.consent_confirmed_at == consent_time
    assert session.consent_text_version == CONSENT_STATEMENT_VERSION
    assert session.consent_capture_sources == REQUIRED_CONSENT_SOURCES
    assert session.started_at == record_time
    assert session.stopped_at == stop_time
    assert session.revision == 5


def test_consent_must_cover_both_capture_sources() -> None:
    session = MeetingSession.new(session_id=SESSION_ID, now=START)

    with pytest.raises(ValueError, match="microphone and system audio"):
        session.confirm_consent(
            (ConsentCaptureSource.MICROPHONE,),
            at=START + timedelta(seconds=1),
        )


def test_invalid_state_transition_is_rejected() -> None:
    session = MeetingSession.new(session_id=SESSION_ID, now=START)

    with pytest.raises(InvalidSessionTransition, match="draft to ready"):
        session.transition(SessionState.READY, at=START + timedelta(seconds=1))


def test_processing_can_retry_without_losing_recording_stop_time() -> None:
    session = MeetingSession.new(session_id=SESSION_ID, now=START)
    session = session.confirm_consent(at=START + timedelta(seconds=1))
    session = session.transition(SessionState.RECORDING, at=START + timedelta(seconds=2))
    session = session.transition(SessionState.RECORDED, at=START + timedelta(minutes=2))
    stopped_at = session.stopped_at

    session = session.transition(SessionState.PROCESSING, at=START + timedelta(minutes=3))
    session = session.transition(SessionState.RECORDED, at=START + timedelta(minutes=4))

    assert session.stopped_at == stopped_at

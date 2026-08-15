import wave
from pathlib import Path

import pytest

from meeting_transcriber.capture.chunks import WavChunkWriter
from meeting_transcriber.capture.devices import AudioDeviceKind
from meeting_transcriber.capture.formats import AudioFormat


def _read_wav(path: Path) -> tuple[int, int, int, bytes]:
    with wave.open(str(path), "rb") as wav_file:
        return (
            wav_file.getframerate(),
            wav_file.getnchannels(),
            wav_file.getnframes(),
            wav_file.readframes(wav_file.getnframes()),
        )


def test_rotates_and_finalizes_fixed_duration_chunks(tmp_path: Path) -> None:
    audio_format = AudioFormat(sample_rate=4, channels=1)
    pcm = b"".join(frame.to_bytes(2, "little", signed=True) for frame in range(10))
    writer = WavChunkWriter(
        tmp_path,
        AudioDeviceKind.MICROPHONE,
        audio_format,
        chunk_duration_seconds=1,
    )

    writer.write_frames(pcm, frame_start_ns=2_000_000_000)
    chunks = writer.close()

    assert [chunk.frame_count for chunk in chunks] == [4, 4, 2]
    assert [chunk.filename for chunk in chunks] == [
        "microphone_0001.wav",
        "microphone_0002.wav",
        "microphone_0003.wav",
    ]
    assert [chunk.start_monotonic_ns for chunk in chunks] == [
        2_000_000_000,
        3_000_000_000,
        4_000_000_000,
    ]
    assert chunks[-1].end_monotonic_ns == 4_500_000_000
    assert b"".join(_read_wav(tmp_path / chunk.filename)[3] for chunk in chunks) == pcm


def test_completed_chunk_is_readable_while_next_chunk_is_open(tmp_path: Path) -> None:
    audio_format = AudioFormat(sample_rate=4, channels=1)
    writer = WavChunkWriter(
        tmp_path,
        AudioDeviceKind.SYSTEM_LOOPBACK,
        audio_format,
        chunk_duration_seconds=1,
    )
    pcm = b"\x01\x00" * 5

    writer.write_frames(pcm, frame_start_ns=0)

    assert len(writer.chunks) == 1
    sample_rate, channels, frame_count, content = _read_wav(tmp_path / "system_loopback_0001.wav")
    assert (sample_rate, channels, frame_count) == (4, 1, 4)
    assert content == b"\x01\x00" * 4
    writer.close()


def test_stereo_frames_are_not_split_at_chunk_boundaries(tmp_path: Path) -> None:
    audio_format = AudioFormat(sample_rate=2, channels=2)
    writer = WavChunkWriter(
        tmp_path,
        AudioDeviceKind.MICROPHONE,
        audio_format,
        chunk_duration_seconds=1,
    )
    pcm = bytes(range(12))

    writer.write_frames(pcm, frame_start_ns=0)
    chunks = writer.close()

    assert [chunk.frame_count for chunk in chunks] == [2, 1]
    assert _read_wav(tmp_path / chunks[0].filename)[3] == pcm[:8]
    assert _read_wav(tmp_path / chunks[1].filename)[3] == pcm[8:]


def test_rejects_partial_frames_before_creating_a_file(tmp_path: Path) -> None:
    writer = WavChunkWriter(
        tmp_path,
        AudioDeviceKind.MICROPHONE,
        AudioFormat(sample_rate=48_000, channels=2),
    )

    with pytest.raises(ValueError, match="complete audio frames"):
        writer.write_frames(b"\x00\x01\x02", frame_start_ns=0)

    assert list(tmp_path.glob("*.wav")) == []


def test_close_is_idempotent_and_prevents_more_writes(tmp_path: Path) -> None:
    writer = WavChunkWriter(
        tmp_path,
        AudioDeviceKind.MICROPHONE,
        AudioFormat(sample_rate=48_000, channels=1),
    )
    writer.write_frames(b"\x00\x00", frame_start_ns=0)

    first_close = writer.close()
    second_close = writer.close()

    assert first_close == second_close
    with pytest.raises(RuntimeError, match="closed"):
        writer.write_frames(b"\x00\x00", frame_start_ns=1)

import pytest

from meeting_transcriber.capture.levels import pcm16_peak


@pytest.mark.parametrize(
    ("pcm", "expected"),
    [
        (b"", 0.0),
        (b"\x00\x00" * 4, 0.0),
        (b"\x00\x40\x00\xc0", 0.5),
        (b"\xff\x7f\x00\x80", 1.0),
    ],
)
def test_pcm16_peak_is_normalized(pcm: bytes, expected: float) -> None:
    assert pcm16_peak(pcm) == expected


def test_pcm16_peak_rejects_partial_samples() -> None:
    with pytest.raises(ValueError, match="complete samples"):
        pcm16_peak(b"\x00")

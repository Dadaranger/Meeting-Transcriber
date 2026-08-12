from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from meeting_transcriber.domain.transcript import (
    TranscriptDocument,
    TranscriptionProfile,
    TranscriptSegment,
    TranscriptSource,
    TranscriptSpeaker,
    TranscriptWord,
)
from meeting_transcriber.processing.evaluation import (
    AccuracyReference,
    AccuracyReferenceSegment,
    AccuracyThresholds,
    evaluate_accuracy,
    main,
    normalize_tokens,
)
from meeting_transcriber.storage.transcript_store import TranscriptStore

SESSION_ID = "844c95f5-e72d-44a9-ad41-f09fcbd7e945"
RUN_ID = "7426bf59-9d54-4323-8142-bbf22d400c90"


def _segment(
    segment_id: str,
    text: str,
    source: TranscriptSource,
    words: tuple[TranscriptWord, ...],
    speaker_id: str | None = None,
) -> TranscriptSegment:
    start_ms = min(word.start_ms for word in words)
    end_ms = max(word.end_ms for word in words)
    speaker_id = speaker_id or ("local" if source is TranscriptSource.MICROPHONE else "remote")
    return TranscriptSegment(
        segment_id=segment_id,
        start_ms=start_ms,
        end_ms=end_ms,
        speaker_id=speaker_id,
        text=text,
        source=source,
        words=words,
    )


def _transcript(segments: tuple[TranscriptSegment, ...]) -> TranscriptDocument:
    speakers = tuple(
        TranscriptSpeaker(
            speaker_id,
            "You" if source is TranscriptSource.MICROPHONE else speaker_id,
            source,
        )
        for speaker_id, source in sorted(
            {(segment.speaker_id, segment.source) for segment in segments},
            key=lambda item: item[0],
        )
    )
    return TranscriptDocument.new(
        SESSION_ID,
        run_id=RUN_ID,
        language="en",
        engine="test",
        model="medium",
        profile=TranscriptionProfile.BALANCED,
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        speakers=speakers,
        segments=segments,
    )


def test_unicode_normalization_handles_punctuation_and_unspaced_cjk() -> None:
    assert normalize_tokens("Project ATLAS, isn't late!") == (
        "project",
        "atlas",
        "isn't",
        "late",
    )
    assert normalize_tokens("你好世界") == ("你", "好", "世", "界")


def test_evaluation_reports_errors_terms_sources_and_timing() -> None:
    transcript = _transcript(
        (
            _segment(
                "ad19b4a2-6f5d-448a-9b43-b73efe73190e",
                "Hello Project Atlas now",
                TranscriptSource.MICROPHONE,
                (
                    TranscriptWord("Hello", 1_050, 1_250),
                    TranscriptWord("Project", 1_300, 1_500),
                    TranscriptWord("Atlas", 1_550, 1_750),
                    TranscriptWord("now", 1_800, 2_050),
                ),
            ),
            _segment(
                "d4967f94-711d-453e-8c76-55a594a8dc88",
                "budget",
                TranscriptSource.SYSTEM_AUDIO,
                (TranscriptWord("budget", 2_100, 2_350),),
            ),
        )
    )
    reference = AccuracyReference(
        SESSION_ID,
        "en",
        (
            AccuracyReferenceSegment(
                1_000,
                2_000,
                TranscriptSource.MICROPHONE,
                "Hello Project Atlas today",
            ),
            AccuracyReferenceSegment(
                2_000,
                3_000,
                TranscriptSource.SYSTEM_AUDIO,
                "budget five",
            ),
        ),
        ("Project Atlas", "budget five"),
    )

    report = evaluate_accuracy(transcript, reference)

    assert report.reference_tokens == 6
    assert report.hypothesis_tokens == 5
    assert report.substitutions == 1
    assert report.deletions == 1
    assert report.insertions == 0
    assert report.word_error_rate == 2 / 6
    assert report.key_term_recall == 0.5
    assert report.source_accuracy == 1.0
    assert report.mean_timing_error_ms == 131.25
    assert len(report.violations(AccuracyThresholds(max_word_error_rate=0.2))) == 1


def test_evaluation_detects_wrong_source_and_silence_hallucinations() -> None:
    spoken = _transcript(
        (
            _segment(
                "4f0afe65-d821-46c2-af76-e657c49c3862",
                "budget approved",
                TranscriptSource.MICROPHONE,
                (
                    TranscriptWord("budget", 100, 300),
                    TranscriptWord("approved", 350, 650),
                ),
            ),
        )
    )
    spoken_reference = AccuracyReference(
        SESSION_ID,
        "en",
        (
            AccuracyReferenceSegment(
                0,
                1_000,
                TranscriptSource.SYSTEM_AUDIO,
                "budget approved",
            ),
        ),
    )

    spoken_report = evaluate_accuracy(spoken, spoken_reference)

    assert spoken_report.word_error_rate == 0.0
    assert spoken_report.source_accuracy == 0.0

    silence_reference = AccuracyReference(SESSION_ID, "en", ())
    silence_report = evaluate_accuracy(spoken, silence_reference)

    assert silence_report.word_error_rate is None
    assert silence_report.hallucinated_tokens == 2
    assert silence_report.violations(AccuracyThresholds(max_hallucinated_tokens=0))


def test_speaker_accuracy_uses_permutation_invariant_remote_labels() -> None:
    transcript = _transcript(
        (
            _segment(
                "975c69b5-aa1b-4a09-ab7f-31dfb23f49a9",
                "alpha project",
                TranscriptSource.SYSTEM_AUDIO,
                (
                    TranscriptWord("alpha", 0, 200),
                    TranscriptWord("project", 250, 500),
                ),
                "remote-2",
            ),
            _segment(
                "60cfdc08-f56d-430b-90a1-405866033750",
                "beta budget",
                TranscriptSource.SYSTEM_AUDIO,
                (
                    TranscriptWord("beta", 600, 800),
                    TranscriptWord("budget", 850, 1_000),
                ),
                "remote-1",
            ),
        )
    )
    reference = AccuracyReference(
        SESSION_ID,
        "en",
        (
            AccuracyReferenceSegment(
                0,
                500,
                TranscriptSource.SYSTEM_AUDIO,
                "alpha project",
                "alex",
            ),
            AccuracyReferenceSegment(
                600,
                1_000,
                TranscriptSource.SYSTEM_AUDIO,
                "beta budget",
                "blair",
            ),
        ),
    )

    report = evaluate_accuracy(transcript, reference)

    assert report.speaker_matches == 4
    assert report.speaker_comparisons == 4
    assert report.speaker_accuracy == 1.0
    assert not report.violations(AccuracyThresholds(min_speaker_accuracy=1.0))


def test_evaluation_cli_loads_artifacts_and_applies_thresholds(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transcript = _transcript(
        (
            _segment(
                "51c3ef49-06a6-4fd8-8182-a2aec485643f",
                "hello world",
                TranscriptSource.MICROPHONE,
                (
                    TranscriptWord("hello", 0, 200),
                    TranscriptWord("world", 250, 500),
                ),
            ),
        )
    )
    transcript_path = TranscriptStore(tmp_path).save_transcript(transcript)
    reference_path = tmp_path / "reference.json"
    reference_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": SESSION_ID,
                "language": "en",
                "key_terms": ["hello world"],
                "segments": [
                    {
                        "start_ms": 0,
                        "end_ms": 500,
                        "source": "microphone",
                        "text": "hello world",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            str(transcript_path),
            str(reference_path),
            "--max-wer",
            "0",
            "--min-key-term-recall",
            "1",
            "--json",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["word_error_rate"] == 0.0
    assert output["threshold_violations"] == []

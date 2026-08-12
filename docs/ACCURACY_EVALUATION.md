# Offline transcription accuracy

Accuracy depends on the meeting language, microphone, background noise, overlapping
speech, names, and technical vocabulary. The project therefore measures accuracy
against short, human-reviewed samples instead of claiming one universal percentage.

## Profiles

| Profile | Local model | Intended use |
| --- | --- | --- |
| Fast | `small` | A quick draft on lower-memory computers |
| Balanced | `medium` | The default accuracy/speed tradeoff |
| Accurate | `large-v3` | Final notes when additional memory and time are acceptable |

All profiles use voice-activity filtering and word timestamps. Adding names and
technical terms in the desktop setup screen gives the decoder vocabulary hints.
Selecting the meeting language explicitly usually avoids language-detection errors.

The first run may download model weights only when the user checks the download
permission. After the model is cached, transcription runs without a network
connection. Audio and transcript data are never sent to a speech API.

## Build a reference sample

Review a representative five-to-ten-minute section of a meeting. Include quiet
speech, remote audio, names, jargon, and a short silent interval. Save the reviewed
text and source labels in a JSON file:

```json
{
  "schema_version": 1,
  "session_id": "844c95f5-e72d-44a9-ad41-f09fcbd7e945",
  "language": "en",
  "key_terms": ["Project Atlas", "WASAPI"],
  "segments": [
    {
      "start_ms": 1000,
      "end_ms": 4200,
      "source": "microphone",
      "text": "Project Atlas uses WASAPI loopback."
    }
  ]
}
```

Use `microphone` for the computer user's mic and `system_audio` for people heard
through the selected output device. An empty `segments` array represents a silence
sample and detects hallucinated tokens.

## Run the evaluator

```powershell
uv run meeting-transcriber-evaluate `
  "C:\path\to\meeting\transcript.json" `
  "C:\path\to\reference.json"
```

It reports:

- word error rate (substitutions + deletions + insertions divided by reviewed words);
- recall for supplied names and technical terms;
- microphone versus system-audio attribution for correctly matched tokens;
- mean timestamp-boundary error for correctly matched tokens; and
- tokens hallucinated in a silence sample.

Punctuation and letter case do not count as errors. CJK ideographs and kana are
tokenized individually so unspaced text remains measurable.

Optional thresholds make the command suitable for repeatable comparison or CI:

```powershell
uv run meeting-transcriber-evaluate transcript.json reference.json `
  --max-wer 0.18 `
  --min-key-term-recall 0.90 `
  --min-source-accuracy 0.95 `
  --max-mean-timing-error-ms 1000 `
  --max-hallucinated-tokens 0 `
  --json
```

The command exits with code `1` if any supplied threshold fails and `2` if either
artifact is invalid. Thresholds are intentionally opt-in: choose values from the
recording conditions and languages that matter for the intended use.

For profile comparisons, transcribe the same recording with each profile and keep
each retained run from `derived/transcripts/`. A profile is an improvement only if
the measured gains justify its extra processing time and memory on the target PC.

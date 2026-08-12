# Meeting Transcriber

Meeting Transcriber is a desktop application that records a meeting's
microphone and computer audio, transcribes the conversation locally, separates
speakers where possible, and exports a structured, human-readable meeting file.

The application is under active development. The first supported platform is
Windows, where WASAPI loopback capture can record the audio played by meeting
applications without requiring a bot to join the call.

## Product direction

- Local-first recording and transcription
- Separate microphone and system-audio capture
- Recoverable sessions that survive an interrupted processing job
- Speaker labels that the user can review and rename
- Markdown as the primary human-readable export
- JSON as the canonical machine-readable record
- Explicit recording/consent reminders and clear privacy controls

The initial release will not depend on Zoom, Teams, Google Meet, or another
meeting provider. It will capture audio at the operating-system level, so it can
work with any meeting application that plays audio through the selected Windows
output device.

## Planning documents

- [Product plan](docs/PRODUCT_PLAN.md)
- [Technical architecture](docs/ARCHITECTURE.md)
- [Delivery roadmap](docs/ROADMAP.md)
- [Development workflow](docs/DEVELOPMENT_WORKFLOW.md)

## Current status

The desktop workflow connects Windows device discovery and recoverable dual-source
capture to consent-gated recording and offline transcription. A meeting draft opens
a device-review screen, recording cannot begin until the acknowledgement is checked,
and the live screen keeps independent source levels, elapsed active time,
pause/resume, and stop controls visible. Consent version, capture scope, session
state, WAV chunks, capture timing, processing jobs, and transcript runs are persisted
locally.

The setup screen can run a consent-gated five-second source test without saving
audio. Recording is blocked at critically low disk space and remaining capacity is
shown throughout capture. The History page identifies abandoned recordings after
restart, only offers recovery when a capture manifest and finalized WAV chunks
exist, and can open the exact local meeting folder.

After capture, History can start a resumable local faster-whisper job using fast,
balanced, or accurate profiles. The user controls the one-time model download;
cached runs require no network. Progress survives at chunk boundaries, cancellation
preserves the recording and prepared audio, and interrupted jobs become retryable at
the next startup. The canonical versioned result is `transcript.json`, with prior
runs retained under `derived/transcripts/`.

Automated checks exercise forced process termination, a simulated 60-minute
dual-source journal, persisted transcription recovery, and deterministic accuracy
metrics. A real 60-minute hardware soak and representative accuracy samples are
still required before a release. Human-readable Markdown export and speaker review
are the next product milestones.

## Run the development application

Install [uv](https://docs.astral.sh/uv/), then from the repository root run:

```powershell
uv sync --extra dev --extra transcription
uv run meeting-transcriber
```

Python 3.12 or 3.13 is supported. The final Windows release will be distributed
as an installer and will not require the user to install Python or run a terminal.

## Run the checks

From PowerShell:

```powershell
.\scripts\check.cmd
```

The same formatting, linting, type, and test checks run on Python 3.12 and 3.13
in GitHub Actions.

## Inspect Windows audio devices

This read-only command lists the microphones and WASAPI loopback inputs available
to the capture backend. It does not open a stream or record audio.

```powershell
uv run meeting-transcriber-audio-devices
```

## Audit a completed capture

This read-only command validates WAV headers, chunk sequence and continuity, and
the 250 ms dual-source alignment target. For an uninterrupted 60-minute hardware
soak, run:

```powershell
uv run meeting-transcriber-capture-audit "C:\path\to\meeting-session" `
  --min-duration-minutes 60 --max-drift-ms 250 --max-gap-ms 0
```

Omit `--max-gap-ms` for a meeting that was intentionally paused. Add `--json` for
machine-readable output. The audit never opens an audio device or modifies the
meeting.

## Measure offline transcription accuracy

Compare `transcript.json` with a short human-reviewed reference to calculate word
error rate, key-term recall, source attribution, timing error, and silence
hallucinations:

```powershell
uv run meeting-transcriber-evaluate transcript.json reference.json
```

See [Offline transcription accuracy](docs/ACCURACY_EVALUATION.md) for the reference
format, profile guidance, and optional pass/fail thresholds.

## Important limitation

Speaker diarization can distinguish voices, but it cannot reliably infer a
person's real name from audio alone. The application will initially label voices
as `You`, `Speaker 1`, `Speaker 2`, and so on, then let the user rename them.

# Meeting Transcriber

Meeting Transcriber is a planned desktop application that records a meeting's
microphone and computer audio, transcribes the conversation locally, separates
speakers where possible, and exports a structured, human-readable meeting file.

The repository is currently in its planning phase. The first supported platform
will be Windows, where WASAPI loopback capture can record the audio played by
meeting applications without requiring a bot to join the call.

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

Milestone 2 is underway. The application foundation, meeting-draft persistence,
and Windows WASAPI microphone/loopback discovery are available. Recording and
transcription are not implemented yet.

## Run the development application

Install [uv](https://docs.astral.sh/uv/), then from the repository root run:

```powershell
uv sync --extra dev
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

## Important limitation

Speaker diarization can distinguish voices, but it cannot reliably infer a
person's real name from audio alone. The application will initially label voices
as `You`, `Speaker 1`, `Speaker 2`, and so on, then let the user rename them.

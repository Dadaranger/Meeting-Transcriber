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

Planning only. No runnable application has been implemented yet.

## Important limitation

Speaker diarization can distinguish voices, but it cannot reliably infer a
person's real name from audio alone. The application will initially label voices
as `You`, `Speaker 1`, `Speaker 2`, and so on, then let the user rename them.

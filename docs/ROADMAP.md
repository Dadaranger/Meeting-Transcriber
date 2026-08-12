# Delivery roadmap

Development is split into independently demonstrable increments. Each increment
is committed and pushed after its relevant checks pass so the Git history shows
how the application evolved.

## Milestone 0 — Planning foundation

**Outcome:** the repository has a shared product definition, architecture, scope,
and delivery sequence.

**Acceptance criteria**

- Product goals and MVP/non-goals are explicit.
- Windows-first capture and local-first processing decisions are documented.
- Reliability, privacy, speaker identity, and packaging risks are visible.
- Later implementation increments have testable completion criteria.

## Milestone 1 — Application foundation

**Outcome:** a developer can run a minimal desktop shell and the repository has
repeatable quality checks.

**Planned increments**

1. Python project layout, dependency locking, and developer commands
2. PySide6 application shell with navigation and diagnostics page
3. Domain session model, state machine, settings paths, and atomic JSON storage
4. Formatting, linting, type checks, unit tests, and CI

**Acceptance criteria**

- One documented command launches the desktop window.
- One documented command runs all local checks.
- Settings and an empty draft session survive an app restart.
- CI runs on every branch/PR.

## Milestone 2 — Windows audio capture spike

**Outcome:** technical risk around dual-source desktop recording is retired before
the full recording UX is built.

**Planned increments**

1. Enumerate microphones and WASAPI render endpoints
2. Capture microphone WAV chunks with level data
3. Capture WASAPI loopback WAV chunks with level data
4. Record both sources with monotonic timestamps
5. Add 10-minute and 60-minute drift/recovery test utilities

**Acceptance criteria**

- Device names and stable identifiers are visible.
- A short test produces audible files for both sources.
- Unplugging/changing a device produces an actionable error.
- A one-hour test stays within the alignment target defined in the product plan.

The exact audio library is selected only after this spike; the capture interface
allows replacing it without changing application or UI code.

## Milestone 3 — Recording MVP

**Outcome:** a user can reliably create and recover a complete meeting recording.

**Planned increments**

1. Consent and preflight screen
2. Dual audio meters and source test
3. Start/pause/resume/stop controls with a recording indicator
4. Chunk manifest and atomic state transitions
5. Interrupted-session detection and recovery
6. Session list and open-folder action

**Acceptance criteria**

- The UI never reports `Recording` until both requested streams are ready.
- Normal stop finalizes the manifest and all chunks.
- Forced termination loses no more than the active chunk.
- A recovered session can proceed to processing.

**Current implementation status:** consent/preflight device review, a timed
no-save source test, live dual meters, disk-space safeguards,
start/pause/resume/stop, atomic state transitions, interrupted-session detection,
artifact-gated recovery, meeting history, and open-folder actions are implemented.
A child-process forced-termination test verifies recovery, and the capture-audit
command enforces chunk integrity and the 250 ms alignment target against a
simulated 60-minute journal. The remaining release evidence is a real 60-minute
Windows hardware soak using that audit command.

## Milestone 4 — Offline transcription

**Outcome:** a recorded session becomes a timestamped transcript without an
external service.

**Planned increments**

1. Model cache and download/progress management
2. faster-whisper adapter and CPU presets
3. Segment/word timestamp persistence
4. Processing progress, cancel, retry, and resumable jobs
5. CPU/CUDA capability diagnostics and benchmarking

**Acceptance criteria**

- A bundled test recording transcribes deterministically enough for assertions.
- Canceling leaves the original recording untouched.
- Re-running with another language/model creates a new processing result.
- CPU-only processing is supported and progress remains responsive.

**Current implementation status:** local model profiles, explicit download
permission, word timestamps, dual-source timeline merge, persisted progress,
cancellation, retry, and interrupted-job recovery are implemented. The remaining
release evidence is deterministic real-model fixture coverage plus target-hardware
CPU/CUDA benchmarking.

## Milestone 5 — Speaker attribution and review

**Outcome:** the transcript distinguishes local and remote speech and can be
corrected by the user.

**Planned increments**

1. Channel-aware local/remote timeline merge
2. Optional diarization adapter for the system stream
3. Anonymous speaker clusters and confidence metadata
4. Transcript review screen, speaker rename, and segment reassignment
5. Overlap and low-confidence visual treatment

**Acceptance criteria**

- Microphone speech defaults to `You`.
- The app remains useful when diarization is disabled or fails.
- Renaming a speaker updates every associated segment without altering audio.
- User corrections survive re-export and are not overwritten silently.

**Current implementation status:** channel-aware local/remote attribution, the
transcript review screen, source-label renaming, segment text correction, reset to
model output, immutable review revisions, Markdown regeneration, optional pinned
Community-1 diarization, stable anonymous remote clusters, exclusive-turn word
assignment, safe fallback, cancellation/retry, runtime diagnostics, and
permutation-invariant speaker-accuracy evaluation are implemented. Manual segment
reassignment, diarization-confidence treatment, real-model fixtures, and overlap
visual treatment remain planned.

## Milestone 6 — Structured export

**Outcome:** each session produces a polished meeting document and canonical data.

**Planned increments**

1. Versioned transcript JSON schema and migrations
2. Deterministic Markdown renderer
3. Editable summary, decisions, and action-item sections
4. Optional pluggable semantic summarizer with explicit privacy disclosure
5. Re-export and open/copy actions

**Acceptance criteria**

- Markdown includes metadata, speakers, timestamps, and transcript.
- JSON validates against its schema.
- Export works without diarization or an AI summary provider.
- Existing user edits are preserved during re-export.

**Current implementation status:** versioned JSON, deterministic baseline Markdown,
retained rendered runs, automatic post-transcription export, and the History open
action are implemented. Editable structured sections and preservation of reviewed
user corrections remain planned.

## Milestone 7 — Desktop release hardening

**Outcome:** a non-technical user can install, run, update, and uninstall the app.

**Planned increments**

1. Windows packaging spike and reproducible build
2. Installer, application icon, version metadata, and clean uninstall behavior
3. First-run hardware/model diagnostics
4. Accessibility and keyboard navigation review
5. Long-session soak tests and failure injection
6. Signed release artifacts and release checklist

**Acceptance criteria**

- A clean Windows machine can install and launch the app without Python.
- The installer explains disk requirements for models and recordings.
- Uninstalling never deletes meeting data without explicit confirmation.
- The release passes the 60-minute end-to-end scenario.

## Later roadmap

- Real-time partial transcripts
- macOS ScreenCaptureKit adapter
- Linux PipeWire/PulseAudio adapter
- Calendar and meeting-platform metadata integrations
- Search across meetings
- Additional exports such as DOCX, PDF, and subtitles
- Opt-in encrypted sync/collaboration

# Product plan

## Vision

Create a dependable desktop companion that turns a live online meeting into a
private, searchable, human-readable record without requiring a meeting bot or a
specific conferencing platform.

The application should make the safe path obvious: confirm consent, choose the
audio sources, record, review speaker labels, and export the result.

## Target user

The first target user is an individual Windows desktop user who participates in
online meetings and wants personal meeting notes. The user may not have a GPU
and should not need to understand speech-model internals.

## Product principles

1. **The recording is the source of truth.** Transcription can be retried without
   recording the meeting again.
2. **Local by default.** Audio and transcripts remain on the user's computer
   unless the user later enables an external processing provider.
3. **Never hide uncertainty.** Unknown speakers, low-confidence words, and failed
   processing stages must be visible and editable.
4. **A failed step must be recoverable.** Recording, transcription, diarization,
   and export are separate resumable stages.
5. **Useful before clever.** A reliable transcript with timestamps is more
   valuable than an unreliable AI-generated summary.
6. **Consent is part of the workflow.** Recording status and consent guidance
   must be prominent rather than buried in settings.

## Primary user journey

1. Open the application.
2. Create a meeting session and enter an optional title.
3. Confirm that the participants have consented to recording.
4. Select a microphone, a speaker/output device, language, model size, and output
   folder.
5. Run a short input test and verify both level meters.
6. Start, pause, resume, and stop recording from an always-visible control.
7. Watch processing progress or defer processing until later.
8. Review the transcript, rename speakers, and correct important text.
9. Export or open the Markdown meeting note and its supporting JSON record.

## MVP scope

### Recording

- Windows 10/11 support
- Microphone capture
- WASAPI loopback capture of the selected output device
- Independent level meters for microphone and system audio
- Start, pause, resume, and stop controls
- Elapsed-time, disk-space, and recording-state indicators
- Chunked recording with a session manifest for crash recovery
- User-selected storage folder

### Processing

- Offline transcription with configurable model size
- Automatic language detection plus an explicit language override
- Word/segment timestamps
- Processing progress, cancellation, retry, and error reporting
- Channel-aware attribution: the microphone channel starts as `You`
- Optional remote-speaker diarization for the mixed system channel
- Editable speaker names

### Output

- A canonical JSON transcript
- A Markdown meeting note containing:
  - title, date, duration, and processing metadata
  - participant/speaker list
  - chronological transcript with timestamps
  - user-editable summary, decisions, and action-item sections
- Re-export after speaker or transcript edits
- A link/button that opens the meeting folder

### Reliability and privacy

- No silent recording
- Consent confirmation before each session
- Local processing as the default
- No telemetry in the first release
- Secrets stored with operating-system credential storage if external providers are
  added later
- Logs that omit transcript and audio content by default

## Explicit non-goals for the first release

- Joining meetings as a bot
- Integrations with conferencing-platform APIs or calendars
- Automatically discovering real speaker names from voices
- Live captions during the meeting
- Mobile support
- Cloud sync, accounts, collaboration, or sharing
- Perfect handling of simultaneous/overlapping speech
- macOS and Linux installers
- Automatic semantic summaries that are required for a successful export

These may become later milestones, but none should block a trustworthy local
recording and transcript workflow.

## Definition of a useful first release

A release candidate is useful when a non-technical Windows user can install it,
record a 60-minute meeting containing both local and remote speech, stop safely,
process the session on CPU, review speaker labels, and open a readable Markdown
file without using a terminal.

## Success measures

- At least 99% of test sessions produce recoverable audio after a normal stop.
- A forced application close loses no more than the active audio chunk.
- Microphone and system streams stay within 250 ms of alignment over 60 minutes.
- The app reports missing/incorrect audio sources before recording.
- Every processing stage can be retried without changing the source recording.
- Exported Markdown remains readable even when diarization or summarization is
  unavailable.

## Risks requiring early validation

| Risk | Why it matters | Early mitigation |
| --- | --- | --- |
| Windows loopback/device changes | Headsets and meeting apps may switch devices mid-call | Build a capture spike before the full UI and surface device loss immediately |
| Clock drift between two devices | Separate microphone and output clocks can desynchronize | Timestamp chunks from a monotonic clock and test one-hour recordings |
| Echo/duplicate local speech | A microphone may pick up speakers while loopback also contains audio | Recommend headphones and keep channels separate until the merge stage |
| Diarization accuracy | Mixed audio and overlapping speech produce incorrect labels | Keep labels editable and make diarization optional/re-runnable |
| CPU/GPU variability | Large models may be unusably slow on some desktops | Offer model presets and benchmark during onboarding |
| Model download size | First use may require several gigabytes | Show exact size, progress, cache location, and cancellation |
| Legal/consent requirements | Recording laws vary by location | Require a consent acknowledgement and avoid pretending the app supplies legal advice |

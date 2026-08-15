# Meeting Transcriber

Record online meetings from your Windows desktop, transcribe them locally, and open
the result as a readable TXT file.

[![Windows CI](https://github.com/Dadaranger/Meeting-Transcriber/actions/workflows/ci.yml/badge.svg)](https://github.com/Dadaranger/Meeting-Transcriber/actions/workflows/ci.yml)
[![Windows package](https://github.com/Dadaranger/Meeting-Transcriber/actions/workflows/windows-package.yml/badge.svg)](https://github.com/Dadaranger/Meeting-Transcriber/actions/workflows/windows-package.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

Meeting Transcriber captures both your microphone and the sound played by meeting
applications. It works at the Windows audio level, so it does not need to join the
call as a bot and is not tied to Zoom, Microsoft Teams, Google Meet, or another
provider.

> **Windows preview:** version 0.1.3 is ready for hands-on testing, but it is not yet
> a fully qualified production release. Review the [known limitations](#known-limitations)
> before relying on it for an important meeting.

## Download for Windows

### [Download the Meeting Transcriber 0.1.3 preview installer](https://github.com/Dadaranger/Meeting-Transcriber/releases/download/v0.1.3-preview.1/Meeting-Transcriber-0.1.3-Setup.exe)

You can also download the
[portable ZIP](https://github.com/Dadaranger/Meeting-Transcriber/releases/download/v0.1.3-preview.1/Meeting-Transcriber-0.1.3-portable.zip)
or [verify the SHA-256 checksums](https://github.com/Dadaranger/Meeting-Transcriber/releases/download/v0.1.3-preview.1/SHA256SUMS.txt).

The installer includes the desktop application, Python runtime, Windows audio
capture, and offline transcription engine. You do not need to install Python or use
a terminal.

## Install in four steps

1. Download `Meeting-Transcriber-0.1.3-Setup.exe` from the link above.
2. Open the downloaded file and follow the setup wizard. Installation is per-user
   and does not require administrator access.
3. Open **Meeting Transcriber** from the Start menu.
4. Complete the first-run **Diagnostics** page to confirm your meeting folder,
   microphone, speaker/output device, and offline transcription runtime.

The preview installer is not yet Authenticode-signed, so Windows may show an
**Unknown publisher** warning. Only continue when the file came from this repository's
official release and, when in doubt, verify it against `SHA256SUMS.txt`.

Upgrading or uninstalling the application does not delete your meetings, transcripts,
or downloaded speech models.

## What it does

- Records your microphone and Windows meeting/system audio separately.
- Works with any meeting application that plays through the selected output device.
- Supports start, pause, resume, stop, audio-level testing, and interrupted-session
  recovery.
- Transcribes recordings on your computer with faster-whisper.
- Offers fast, balanced, and best-accuracy transcription profiles.
- Retries unusually quiet speech with a more sensitive detection pass.
- Creates timestamped, plain-text meeting notes that open in Notepad and other
  ordinary text editors.
- Lets you correct transcript text, speaker assignments, speaker names, summaries,
  decisions, and action items.
- Can optionally separate remote voices when the pyannote runtime and gated model
  are available.

## Record and transcribe a meeting

1. Tell everyone that the meeting will be recorded and obtain any consent required
   where you live and work.
2. Select **New meeting**, give the meeting a clear name, and choose the microphone
   and meeting/system-audio devices.
3. Select the consent acknowledgement and use **Test sources**. Confirm that both
   level meters react before recording.
4. Start the recording. Pause or resume when needed, then stop when the meeting ends.
5. Open **History**, select the meeting, and choose offline transcription.
6. Select a language and accuracy profile. The first run for a profile requires you
   to allow its speech-model download; later runs use the local cache.
7. When processing finishes, choose **Open saved TXT** to open the readable meeting
   file directly.

The consent acknowledgement records your confirmation inside the meeting session.
It does not independently verify participant consent or guarantee compliance with
recording laws or workplace policies.

## Files and privacy

Audio, transcripts, corrections, and TXT notes stay on your computer. The application
has no account, cloud sync, or telemetry. A network connection is needed only when
you explicitly approve a speech-model download.

By default, meetings are stored in:

```text
Documents\Meeting Transcriber\Meetings\Meeting name - YYYY-MM-DD HHMMSS\
```

Each folder can contain:

- `audio\` — recoverable microphone and system-audio WAV chunks
- `Meeting name - YYYY-MM-DD HHMMSS.txt` — the human-readable result
- `transcript.json` — the canonical structured transcript
- `session.json` and `capture.json` — session and recording metadata
- `derived\` — retained processing, review, and export revisions

You can change the meetings folder from **Diagnostics**. History provides buttons
that open the exact meeting folder and the saved TXT file.

## System requirements

- Windows 10 version 1809 or newer, or Windows 11
- A 64-bit-compatible Windows computer
- A microphone and a Windows playback/output device
- Enough free disk space for WAV recordings and the selected speech model
- Internet access for the first approved download of each speech model

Transcription runs on the CPU when no supported GPU runtime is available. Larger
accuracy profiles require more storage, memory, and processing time.

## Troubleshooting

**The application opens but I cannot record**

Open **Diagnostics**, refresh the audio devices, and confirm that both a microphone
and a Windows loopback/output device are available. On the recording page, select the
devices again and run the five-second source test.

**Remote participants are missing from the recording**

Select the same Windows output device that the meeting application uses. Play meeting
audio and confirm that the meeting/system-audio level meter moves before recording.

**Transcription says that no dialogue was detected**

Open the meeting folder and confirm that the WAV files contain audible speech. Retry
with the correct language selected. The application automatically makes one more
sensitive pass for valid but quiet recordings.

**A speech model could not be downloaded**

Confirm that the download checkbox is selected, check the internet connection and
available disk space, then retry. Partial model downloads are retained so a retry can
continue rather than discarding the recording.

**Where is my TXT file?**

Open **History**, select the completed meeting, and choose **Open saved TXT** or
**Open folder**.

## Known limitations

- This preview supports Windows only.
- The installer is not yet Authenticode-signed.
- Transcription is not perfect. Review important names, numbers, decisions, and
  action items against the recording.
- The application cannot infer a person's real name from their voice. Remote voices
  begin with generic speaker labels that you can rename.
- Overlapping speech and noisy or very quiet audio can reduce accuracy.
- Optional individual remote-speaker separation requires an additional pyannote
  runtime, acceptance of the model's Hugging Face terms, and a one-time model
  download. Normal transcription still works without it.
- Clean-machine Windows 10/11, long-duration hardware, accessibility, and broader
  language/accuracy qualification remain open release gates.

See the [Windows MVP release evidence](docs/RELEASE_EVIDENCE.md) for the exact tested
scope and remaining qualification work.

## For developers

Python 3.12 and 3.13 are supported. Install
[uv](https://docs.astral.sh/uv/), then run:

```powershell
uv sync --extra dev --extra transcription --extra diarization
uv run meeting-transcriber
```

Run formatting, linting, strict type checks, and all tests with:

```powershell
.\scripts\check.cmd
```

Build the frozen Windows application and installer with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_installer.ps1 -AppVersion 0.1.3
```

More project documentation:

- [Product plan](docs/PRODUCT_PLAN.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Development workflow](docs/DEVELOPMENT_WORKFLOW.md)
- [Accuracy evaluation](docs/ACCURACY_EVALUATION.md)
- [Release checklist](docs/RELEASE_CHECKLIST.md)

## License

Meeting Transcriber is free software licensed under the
[GNU General Public License v3.0](LICENSE).

# macOS support and qualification

Version 0.1.4 adds native macOS packaging for both Apple Silicon (`arm64`) and Intel
(`x86_64`). Each download is a self-contained application; users do not install
Python, Swift, Homebrew, or speech-processing packages.

## Capture design

- Microphones are discovered and recorded through Core Audio.
- Meeting/system audio is received through Apple's ScreenCaptureKit framework.
- A bundled Swift helper converts ScreenCaptureKit buffers to 48 kHz stereo signed
  16-bit PCM before the existing recoverable WAV-chunk recorder sees them.
- The helper excludes Meeting Transcriber's own process audio and does not request or
  persist screen frames.
- Microphone and meeting audio remain separate through capture and transcription.

macOS therefore asks for both **Microphone** and **Screen & System Audio Recording**
access. ScreenCaptureKit permission changes take effect after the application is
reopened.

## Automated evidence required for every download

The desktop package workflow runs independently on GitHub's native Apple Silicon and
Intel macOS runners. Each runner must:

1. install the locked Python 3.12 dependency graph;
2. pass the complete Python regression suite;
3. compile the Swift helper with macOS 13 as its deployment target;
4. build and verify the `.app` bundle;
5. execute the real packaged entry point and verify its smoke marker;
6. create architecture-labelled DMG and ZIP files; and
7. publish checksums and build-provenance attestations.

A version tag is not published until the Windows build and both Mac builds succeed.

## Preview limitations and manual release gates

The automated runners cannot exercise interactive macOS privacy prompts or prove that
audio is audible on physical hardware. Before calling the Mac build production-ready,
record candidate-specific evidence for:

- first launch and permission recovery on a clean Mac;
- audible microphone and meeting/system WAV chunks;
- pause, resume, stop, transcription, review, and TXT export;
- a 60-minute dual-source capture and alignment audit;
- Apple Developer ID signing and notarization; and
- VoiceOver, keyboard navigation, scaling, and high-contrast behavior.

Until signing credentials and those hardware results exist, the Mac download is an
ad-hoc-signed preview and requires the documented Control-click **Open** process.

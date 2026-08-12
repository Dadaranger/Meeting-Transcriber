# Windows release checklist

The build workflow produces a per-user Inno Setup installer, a portable archive,
SHA-256 checksums, and GitHub build-provenance attestations. A tag matching `v*`
also publishes those files to a GitHub Release.

Record candidate-specific results and workflow links in
[`RELEASE_EVIDENCE.md`](RELEASE_EVIDENCE.md).

## Before tagging

- [ ] Update `pyproject.toml`, `meeting_transcriber.__version__`,
  `packaging/windows-version.txt`, and the installer default to the same version.
- [ ] Run `scripts/check.cmd` from a clean locked environment.
- [ ] Run `scripts/release_readiness.ps1` and retain its workflow/run link.
- [ ] Run `scripts/build_windows.ps1` on Windows and confirm the packaged smoke test passes.
- [ ] Run `scripts/build_installer.ps1 -AppVersion <version>`.
- [ ] Install for the current user on a clean Windows 10/11 x64 VM with no Python.
- [ ] Confirm microphone and WASAPI loopback discovery, consent, recording, pause/resume,
  stop, offline transcription, review, Markdown export, meeting-history recovery, and
  persistence of a user-selected meeting folder across restart.
- [ ] Complete a real 60-minute dual-source recording and transcription soak on target
  hardware; record CPU, memory, disk use, temperatures, failures, and accuracy results.
- [ ] Verify a model download requires explicit approval and that a cached model works offline.
- [ ] Verify keyboard-only navigation, visible focus, screen-reader labels, scaling at 200%,
  and high-contrast behavior.
- [ ] Uninstall and confirm application files are removed while recordings, transcripts,
  reviews, notes, and downloaded models remain intact.

## Signing and publication

- [ ] Configure `WINDOWS_SIGNING_CERTIFICATE` as the base64-encoded PFX and
  `WINDOWS_SIGNING_PASSWORD` as repository secrets, or clearly label the build unsigned.
- [ ] Create and push an annotated version tag after every required check above passes.
- [ ] Confirm Authenticode signatures and timestamp chains on both the application and installer.
- [ ] Verify `SHA256SUMS.txt` against the published files.
- [ ] Verify GitHub artifact attestations with `gh attestation verify`.
- [ ] Install the exact published installer on a second clean Windows machine and repeat
  launch, record, transcribe, export, and uninstall smoke tests.

Do not mark a release production-ready from automated tests alone. Hardware capture,
gated-model behavior, code-signing identity, and the long-session scenario require real
release evidence.

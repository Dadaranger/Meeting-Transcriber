# Windows MVP release evidence

This ledger records what is proven for the current Windows MVP and what still needs
real hardware, model access, signing credentials, or a clean test machine. It is an
evidence record, not a substitute for [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).

## Audited application checkpoint

- Application commit: `6758a73` (`codex/storage-location-settings`, PR
  [#15](https://github.com/Dadaranger/Meeting-Transcriber/pull/15))
- Audit host: Windows 10 `10.0.19045`, Python 3.13.9
- Audit date: 2026-08-11
- Release version under test: `0.1.0`

## Automated evidence

| Requirement | Evidence | Result |
| --- | --- | --- |
| Repeatable locked checks | `scripts/release_readiness.ps1` ran the locked sync, formatting, lint, strict typing, full tests, failure gates, and frozen build | Pass |
| Unit, integration, and UI behavior | 157 tests | Pass |
| Failure injection and synthetic one-hour audit | 29 selected tests from [`FAILURE_TEST_MATRIX.md`](FAILURE_TEST_MATRIX.md) | Pass |
| Windows CI on Python 3.12 and 3.13 | GitHub Actions run [31567017037](https://github.com/Dadaranger/Meeting-Transcriber/actions/runs/31567017037) | Pass |
| Frozen desktop runtime | Local clean PyInstaller build and the extracted hosted portable `MeetingTranscriber.exe --package-smoke-test` | Pass; hosted process exit code 0 and version 0.1.0 |
| Installer compilation, archive, checksums, provenance, and artifact upload | GitHub Actions run [31567017072](https://github.com/Dadaranger/Meeting-Transcriber/actions/runs/31567017072) completed every required untagged step | Pass |
| Hosted artifact identity | `meeting-transcriber-windows-31567017072`, 224,055,718-byte upload containing the installer, portable ZIP, and checksum manifest | Pass |
| Hosted artifact integrity | Downloaded both files; calculated SHA-256 values matched `SHA256SUMS.txt`; `gh attestation verify` succeeded for each file | Pass |
| Real Windows device enumeration | `meeting-transcriber-audio-devices` found two WASAPI microphone inputs and one default 48 kHz WASAPI loopback endpoint without opening a stream | Pass for discovery only |

The automated tests cover consent-gated dual-source capture orchestration, pause and
resume, chunk finalization, interrupted-session recovery, offline-only cache failure,
transcription retry/cancellation, source-aware timeline merge, optional diarization
fallback, review revisions, segment speaker assignment, structured-note re-export,
user-selected meeting storage, and Markdown/JSON persistence. The tests use fake or
synthetic devices and engines where live hardware or model weights would be required.

## External qualification gates

These rows must contain retained evidence before an annotated version tag is pushed.
Do not turn a row into “pass” based only on unit tests or a simulated device.

| Gate | Required evidence | Status |
| --- | --- | --- |
| Clean-machine installer | Install and launch the exact GitHub artifact on Windows 10 and Windows 11 x64 with no Python | Not run |
| Real meeting capture | Microphone and selected WASAPI loopback both produce audible finalized chunks; device loss is reported clearly | Device discovery passed; recording not run |
| Long-session reliability | Real uninterrupted 60-minute dual-source meeting plus capture-audit output showing no gaps and no more than 250 ms drift | Not run |
| Offline speech accuracy | Human-reviewed representative samples for each intended language/profile with evaluator JSON, timing, CPU, memory, and real-time factor | Not run; no speech-model cache was present on the audit host |
| Cached/offline model behavior | Explicitly approve one model download, disconnect networking, then transcribe from the retained cache | Not run |
| Optional remote-speaker model | Accept the gated Community-1 terms, use a temporary token, verify local inference and retained fallback behavior | Not run |
| Accessibility | Keyboard-only run, visible focus, screen-reader labels, 200% scaling, and Windows high-contrast evidence | Automated names/shortcuts pass; assistive-technology run not performed |
| Uninstall data safety | Uninstall and prove application files are removed while meetings, transcripts, reviews, notes, and model caches remain | Not run |
| Authenticode | Configure the certificate secrets and verify application/installer signatures and timestamp chains | Hosted application and installer confirmed `NotSigned`; secrets not configured |
| Tagged release publication | After every external gate passes, push the annotated tag and verify the exact published release artifacts again | Waiting for external gates; no tag created |

## Release decision

The code and frozen bundle are suitable for continued release-candidate testing, but
the application must not yet be labeled production-ready or tagged as a completed
release. The external gates above protect the claims that a non-technical user can
install it, record real Windows meeting audio for an hour, transcribe accurately on
the target hardware, use it accessibly, and uninstall it without data loss.

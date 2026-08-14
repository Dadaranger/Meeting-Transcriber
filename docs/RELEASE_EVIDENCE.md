# Windows MVP release evidence

This ledger records what is proven for the current Windows MVP and what still needs
real hardware, model access, signing credentials, or a clean test machine. It is an
evidence record, not a substitute for [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).

## Audited application checkpoint

- Application commit: `e7fd79d` (`codex/model-download-readiness`, PR
  [#17](https://github.com/Dadaranger/Meeting-Transcriber/pull/17))
- Audit host: Windows 10 `10.0.19045`, Python 3.13.9
- Audit date: 2026-08-13
- Release version under test: `0.1.1`

## Automated evidence

| Requirement | Evidence | Result |
| --- | --- | --- |
| Repeatable locked checks | Locked formatting, lint, strict typing, full tests, and a clean frozen build ran on the audited commit | Pass |
| Unit, integration, and UI behavior | 173 tests | Pass |
| Failure injection and synthetic one-hour audit | 29 selected tests from [`FAILURE_TEST_MATRIX.md`](FAILURE_TEST_MATRIX.md) | Pass |
| Windows CI on Python 3.12 and 3.13 | GitHub Actions PR run [31776892067](https://github.com/Dadaranger/Meeting-Transcriber/actions/runs/31776892067) and push run [31776889651](https://github.com/Dadaranger/Meeting-Transcriber/actions/runs/31776889651) | Pass |
| Frozen desktop runtime | Clean local PyInstaller build ran the marker-backed package smoke test and a normal launch produced a responding `Meeting Transcriber` window | Pass |
| Installer compilation, archive, checksums, provenance, and artifact upload | GitHub Actions run [31776892048](https://github.com/Dadaranger/Meeting-Transcriber/actions/runs/31776892048) completed every required untagged step | Pass |
| Hosted artifact identity | `meeting-transcriber-windows-31776892048`, containing the 84,740,013-byte installer, 133,768,262-byte portable ZIP, and checksum manifest | Pass |
| Hosted artifact integrity | Downloaded both files; calculated SHA-256 values matched `SHA256SUMS.txt`; `gh attestation verify` succeeded for each file | Pass |
| Current-host installer upgrade | Installed the exact hosted 0.1.1 artifact over 0.1.0 and ran the marker-backed installed-package smoke test | Pass; installer and smoke exit codes 0; file/product version and marker all report `0.1.1`; three meetings and the completed medium model remained |
| Packaging regression defense | The build now freezes `meeting_transcriber.__main__` and requires versioned marker evidence from the executing entry point | Pass; a definitions-only executable can no longer satisfy the smoke check |
| Desktop layout and reflow | Captured Home, History, Diagnostics, Recording Setup, Offline Transcription, and Transcript Review in the native Windows backend at the 960×640 application minimum; geometry tests require readable wrapped text, vertical scrolling, no horizontal overflow, and uncompressed actions | Pass; Diagnostics actions stack below cards, History actions fit their columns, and Review uses full-width stacked editors |
| Uninstall model-cache safety | Counted the two local model-cache roots before and after isolated uninstall | Pass; all 14 files remained and the isolated application directory was removed |
| Real Windows device enumeration | `meeting-transcriber-audio-devices` found two WASAPI microphone inputs and one default 48 kHz WASAPI loopback endpoint without opening a stream | Pass for discovery only |
| Explicit model acquisition | Real `small` and `medium` snapshot acquisition completed; `medium` reported progress through 1,530,571,735 allowed-file bytes and then loaded with `local_files_only=True` | Pass |
| Real offline transcription fixture | A fresh cache-only CPU/int8 engine transcribed [OpenAI Whisper's 10.36-second English JFK fixture](https://github.com/openai/whisper/blob/main/tests/jfk.flac) in 7.8 seconds | Pass for this single fixture |

Hosted PR #17 run `31776892048` artifact SHA-256 values:

- Portable ZIP: `e13d53b5db1d2651dcea062839cde536035c9e936fc6aabfe5234516c9605340`
- Installer: `bff51fd4c32598352fe6f5c2cfa74232796695308d0f2bb1ae293b65f152bf88`

The earlier hosted artifact from run `31570993081` is superseded. Its frozen
executable used `main.py` as the PyInstaller script, which defined `main()` but did
not invoke it. That executable therefore exited successfully without showing a
window, and its old exit-code-only package smoke check was a false positive. Commit
`cb5f175` corrects the entry point and makes the smoke test require evidence written
by the running application. The exact corrected hosted installer was also launched
through the normal no-argument path on the audit host.

The installed app then exposed a separate desktop-layout defect: generic widget
background styling painted opaque bands behind labels, while several page layouts
compressed content below its readable height. Commit `9e3c5ce` makes ordinary labels
and checkboxes transparent, gives checkboxes a visible state outline, reflows Home
and History content, and adds shared vertical scrolling to Recording, Transcription,
and Review. Minimum-window regression tests and native before/after renders cover all
six primary pages. Screenshot evidence does not replace the outstanding manual
screen-reader, 200%-scaling, high-contrast, and keyboard-only gates.

A second minimum-window audit caught remaining width pressure that page-only renders
had missed. Commit `87289eb` makes Diagnostics vertically responsive, simplifies and
spans History actions, wraps long page titles, and stacks the Review form, notes, and
segment editor. The native 960×640 renders and updated UI assertions cover the real
main-window width after its 250-pixel sidebar.

The first `medium` attempt also exposed an unreliable automatic Xet transfer: it
preallocated an opaque temporary model file and left a duplicate after interruption.
Commit `34e18da` forces the standard progress-reporting HTTPS path, preserves the real
exception type and a redacted error detail, gives retry guidance, and excludes Xet
from new Windows bundles. The HTTPS acquisition completed in 258 seconds; a fresh
offline CPU/int8 engine loaded the retained snapshot and processed both recorded
microphone WAV chunks. Those low-level test chunks contained no recognizable speech,
so this is a runtime/cache proof rather than an accuracy sample. Commit `e7fd79d`
removes the obsolete Xet directory during upgrades; the final installed audit proved
it absent while the completed model and meeting files remained.

## Real small-model qualification

- Approved profile/model: Fast / `small` (`Systran/faster-whisper-small`)
- Snapshot commit: `536b0662742c02347bc0e980a01041f333bce120`
- Retained model payload: 486,212,372 bytes (about 463.7 MiB)
- Host/runtime: Windows 10, CPU, int8, beam size 1, English selected
- First acquisition through the application manager: 125.629 seconds; progress began
  at zero, advanced in roughly one-percent persisted increments, and completed at the
  exact payload size
- Cache-only repeat: a fresh engine with `HF_HUB_OFFLINE=1`, downloads disabled, and
  local-only model loading completed the 10.36-second clip in 7.8 seconds (real-time
  factor 0.753)
- Transcript: “And so my fellow Americans, ask not what your country can do for you,
  ask what you can do for your country.”
- Human reference comparison: 22 reference tokens, 22 hypothesis tokens, zero
  substitutions, deletions, or insertions; WER 0.0; both supplied key terms matched;
  segment confidence 0.94447
- Limitation: this is a short, clean, single-speaker English fixture. It proves the
  real download/cache path and a deterministic smoke-level accuracy result, not
  representative meeting, multilingual, noisy-audio, diarization, or timing accuracy.
- Windows cache note: Hugging Face used its supported degraded non-symlink cache mode
  because Developer Mode was not enabled; this can consume additional disk space but
  did not prevent acquisition or offline inference.

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
| Clean-machine installer | Install and launch the exact GitHub artifact on Windows 10 and Windows 11 x64 with no Python | Current-host exact-installer normal launch passed; separate clean Windows 10/11 machines not run |
| Real meeting capture | Microphone and selected WASAPI loopback both produce audible finalized chunks; device loss is reported clearly | Inconclusive: one 50-second local test produced 45 seconds of low-level microphone audio and 0.02 seconds from an idle loopback; repeat with audible system playback is required |
| Long-session reliability | Real uninterrupted 60-minute dual-source meeting plus capture-audit output showing no gaps and no more than 250 ms drift | Not run |
| Offline speech accuracy | Human-reviewed representative samples for each intended language/profile with evaluator JSON, timing, CPU, memory, and real-time factor | Partial: one clean English `small` fixture passed at WER 0.0 and RTF 0.753; representative meetings and other profiles/languages remain |
| Cached/offline model behavior | Explicitly approve one model download, disconnect networking, then transcribe from the retained cache | Partial: explicit real `small` and `medium` downloads and fresh `HF_HUB_OFFLINE=1` local-only engines passed; physical network-disconnect UI run remains |
| Optional remote-speaker model | Accept the gated Community-1 terms, use a temporary token, verify local inference and retained fallback behavior | Not run |
| Accessibility | Keyboard-only run, visible focus, screen-reader labels, 200% scaling, and Windows high-contrast evidence | Automated names/shortcuts pass; assistive-technology run not performed |
| Uninstall data safety | Uninstall and prove application files are removed while meetings, transcripts, reviews, notes, and model caches remain | Partial: isolated current-host uninstall removed the app and preserved all 14 model-cache files; no meeting-data root existed on this host |
| Authenticode | Configure the certificate secrets and verify application/installer signatures and timestamp chains | Hosted application and installer confirmed `NotSigned`; secrets not configured |
| Tagged release publication | After every external gate passes, push the annotated tag and verify the exact published release artifacts again | Waiting for external gates; no tag created |

## Release decision

The code and frozen bundle are suitable for continued release-candidate testing, but
the application must not yet be labeled production-ready or tagged as a completed
release. The external gates above protect the claims that a non-technical user can
install it, record real Windows meeting audio for an hour, transcribe accurately on
the target hardware, use it accessibly, and uninstall it without data loss.

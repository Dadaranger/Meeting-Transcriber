# Failure and soak-test matrix

Automated tests intentionally exercise recoverable failures without requiring live
meeting hardware. Run the complete automated gate on Windows with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\release_readiness.ps1
```

| Scenario | Automated evidence | Expected result |
| --- | --- | --- |
| Microphone or loopback disappears mid-recording | `test_dual_source_recorder.py` | Both workers stop; capture is interrupted; finalized chunks remain recoverable. |
| Capture start or stop throws | `test_recording_service.py` | Session becomes interrupted instead of falsely recorded. |
| Process is forcibly terminated | `test_forced_termination_recovery.py` | Next launch detects abandoned recording state and exposes recovery only with finalized audio. |
| Offline model raises, then retry succeeds | `test_transcription_service.py` | Failed job preserves prepared audio and retry reuses the same run safely. |
| TXT write fails after transcription | `test_transcription_service.py` | Retry reuses the completed transcript without another model pass. |
| Optional diarization fails | `test_transcription_service.py` | The combined remote-speaker transcript still completes with a visible warning. |
| Model is absent while offline | `test_transcription_engine.py` | The job reports a local-cache error and never silently downloads. |
| Sixty-minute dual-source timeline | `test_capture_audit.py` | Synthetic one-hour sources stay within 250 ms alignment with no sequence gap. |
| Excess drift, sequence gap, overlap, or WAV mismatch | `test_capture_audit.py` | The audit fails and names the violated release threshold. |

Automation cannot prove Windows driver stability, thermal behavior, real clock drift,
speech accuracy, screen-reader behavior, or clean-machine install/uninstall. Record
those results against [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) for every release
candidate; do not replace them with the synthetic matrix.

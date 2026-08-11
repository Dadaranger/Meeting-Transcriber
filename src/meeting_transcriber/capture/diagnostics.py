from __future__ import annotations

import json
from dataclasses import asdict

from meeting_transcriber.capture.windows_pyaudio import PyAudioWPatchDeviceBackend


def main() -> int:
    """Print the current Windows capture catalog without opening a recording stream."""
    catalog = PyAudioWPatchDeviceBackend().discover_devices()
    document = {
        "microphones": [asdict(device) for device in catalog.microphones],
        "loopbacks": [asdict(device) for device in catalog.loopbacks],
    }
    print(json.dumps(document, indent=2, default=str))
    return 0

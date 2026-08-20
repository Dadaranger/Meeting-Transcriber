from __future__ import annotations

import json
from dataclasses import asdict

from meeting_transcriber.capture.platform_backend import create_platform_capture_backend


def main() -> int:
    """Print the current desktop capture catalog without opening a recording stream."""
    catalog = create_platform_capture_backend().devices.discover_devices()
    document = {
        "microphones": [asdict(device) for device in catalog.microphones],
        "loopbacks": [asdict(device) for device in catalog.loopbacks],
    }
    print(json.dumps(document, indent=2, default=str))
    return 0

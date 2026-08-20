from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main() -> int:
    repository_root = Path(__file__).resolve().parent.parent
    source = repository_root / "src" / "meeting_transcriber" / "assets" / "meeting-transcriber.svg"
    destination = repository_root / "build" / "macos" / "MeetingTranscriber.iconset"
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise RuntimeError(f"Could not load icon source: {source}")

    destination.mkdir(parents=True, exist_ok=True)
    icons = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1_024,
    }
    for filename, size in icons.items():
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(QColor(Qt.GlobalColor.transparent))
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        if not image.save(str(destination / filename), "PNG"):
            raise RuntimeError(f"Qt could not write macOS icon asset: {filename}")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

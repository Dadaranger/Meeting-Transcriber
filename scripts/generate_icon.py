from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def main() -> int:
    repository_root = Path(__file__).resolve().parent.parent
    source = repository_root / "src" / "meeting_transcriber" / "assets" / "meeting-transcriber.svg"
    destination = repository_root / "packaging" / "meeting-transcriber.ico"
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise RuntimeError(f"Could not load icon source: {source}")
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(QColor(Qt.GlobalColor.transparent))
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(destination), "ICO"):
        raise RuntimeError("Qt could not write the Windows ICO asset")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

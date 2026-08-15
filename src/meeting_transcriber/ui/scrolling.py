from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget


class VerticalScrollContent(QWidget):
    """Preserve content height while allowing it to reflow to the viewport width."""

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(0, hint.height())

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())


def create_scrollable_page(
    owner: QWidget,
    *,
    accessible_name: str,
    margins: tuple[int, int, int, int],
    spacing: int,
) -> tuple[QVBoxLayout, QScrollArea]:
    """Create a vertically scrollable page that never compresses its content."""
    outer = QVBoxLayout(owner)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    scroll_area = QScrollArea(owner)
    scroll_area.setObjectName("pageScrollArea")
    scroll_area.setAccessibleName(accessible_name)
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QFrame.Shape.NoFrame)
    scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    content = VerticalScrollContent()
    content.setObjectName("pageScrollContent")
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(*margins)
    content_layout.setSpacing(spacing)

    scroll_area.setWidget(content)
    outer.addWidget(scroll_area)
    return content_layout, scroll_area


def reset_scroll_position(scroll_area: QScrollArea) -> None:
    """Return a page to the top after its visible content changes."""
    scroll_bar = scroll_area.verticalScrollBar()
    scroll_bar.setValue(scroll_bar.minimum())

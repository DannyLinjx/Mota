"""Mota line-art brand mark drawn with QPainter."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from mota.desktop.theme.tokens import COLORS


class BrandMark(QWidget):
    def __init__(self, size: int = 28, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(COLORS["accent"]), 2.4))
        w, h = self.width(), self.height()
        path = [QPointF(3, h - 4), QPointF(3, 4), QPointF(w * 0.5, h * 0.55),
                QPointF(w - 3, 4), QPointF(w - 3, h - 4)]
        for index in range(len(path) - 1):
            painter.drawLine(path[index], path[index + 1])
        painter.setBrush(QColor(COLORS["selected"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(w - 6, h - 8, 4, 4))
        painter.end()

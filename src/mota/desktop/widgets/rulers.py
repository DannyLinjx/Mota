"""Adaptive millimetre rulers sharing the canvas world transform."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from mota.desktop.theme.tokens import COLORS


def nice_step(span_mm: float, pixels_per_mm: float, target_px: float = 90.0) -> float:
    if span_mm <= 0 or pixels_per_mm <= 0:
        return 1.0
    target_mm = target_px / pixels_per_mm
    exponent = math.floor(math.log10(max(target_mm, 1e-9)))
    for factor in (1, 2, 5, 10):
        step = factor * (10 ** exponent)
        if step >= target_mm * 0.8:
            return step
    return 10 ** (exponent + 1)


class RulerWidget(QWidget):
    def __init__(self, orientation: str, view, parent=None) -> None:
        super().__init__(parent)
        self.orientation = orientation
        self.view = view
        if orientation == "top":
            self.setFixedHeight(22)
        else:
            self.setFixedWidth(22)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS["shell"]))
        painter.setPen(QPen(QColor(COLORS["border"]), 1))
        if self.orientation == "top":
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        else:
            painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        transform = self.view.transform()
        pixels_per_mm = abs(transform.m11())
        if pixels_per_mm <= 0:
            return
        scene_rect = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        font = QFont()
        font.setPixelSize(9)
        painter.setFont(font)
        painter.setPen(QPen(QColor(COLORS["textMuted"]), 1))
        step = nice_step((scene_rect.width() if self.orientation == "top"
                          else scene_rect.height()), pixels_per_mm)
        minor = step / 5.0
        if self.orientation == "top":
            start = math.floor(scene_rect.left() / minor) * minor
            value = start
            while value <= scene_rect.right() + minor:
                screen_x = self.view.mapFromScene(QPointF(value, 0)).x()
                is_major = abs((value / step) - round(value / step)) < 1e-6
                tick = 8 if is_major else 4
                painter.drawLine(screen_x, self.height() - tick, screen_x, self.height() - 1)
                if is_major:
                    painter.drawText(QRectF(screen_x + 2, 1, 60, 12),
                                     Qt.AlignmentFlag.AlignLeft,
                                     f"{value:g}")
                value += minor
        else:
            start = math.floor(scene_rect.top() / minor) * minor
            value = start
            while value <= scene_rect.bottom() + minor:
                screen_y = self.view.mapFromScene(QPointF(0, value)).y()
                is_major = abs((value / step) - round(value / step)) < 1e-6
                tick = 8 if is_major else 4
                painter.drawLine(self.width() - tick, screen_y, self.width() - 1, screen_y)
                if is_major:
                    painter.save()
                    painter.translate(2, screen_y - 3)
                    painter.rotate(-90)
                    painter.drawText(QRectF(0, 0, 60, 12), Qt.AlignmentFlag.AlignLeft,
                                     f"{-value:g}")
                    painter.restore()
                value += minor
        painter.end()

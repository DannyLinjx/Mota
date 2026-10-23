"""Welcome page with the Mota entry animation (native QPainter + Qt animations)."""

from __future__ import annotations

import math

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, Qt, QTimer,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from mota import __version__
from mota.desktop.icon import mota_icon
from mota.desktop.theme.tokens import COLORS

TRACES = [
    [(-40, 620), (180, 620), (230, 570), (230, 430), (300, 360), (520, 360)],
    [(1450, 250), (1240, 250), (1180, 310), (1180, 480), (1090, 570), (860, 570)],
    [(-30, 180), (170, 180), (250, 260), (430, 260), (500, 330), (500, 470)],
    [(1480, 690), (1330, 690), (1270, 630), (1270, 520), (1180, 430), (1010, 430)],
    [(700, -30), (700, 110), (760, 170), (930, 170), (990, 230), (990, 330)],
    [(820, 900), (820, 760), (880, 700), (1050, 700)],
]


class WelcomeCanvas(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.progress = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(9000)
        self.animation.setLoopCount(1)
        self.animation.valueChanged.connect(self._update)
        self.reduced_motion = False

    def _update(self, value) -> None:
        self.progress = float(value)
        self.update()

    def start(self) -> None:
        if not self.reduced_motion:
            self.animation.start()

    def stop(self) -> None:
        self.animation.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if (not self.reduced_motion
                and self.animation.state() == QVariantAnimation.State.Paused):
            self.animation.resume()

    def hideEvent(self, event) -> None:  # noqa: N802
        if self.animation.state() == QVariantAnimation.State.Running:
            self.animation.pause()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(COLORS["background"]))
        gradient.setColorAt(1.0, QColor("#0E171D"))
        painter.fillRect(self.rect(), gradient)
        width, height = self.width(), self.height()
        # low-contrast grid
        painter.setPen(QPen(QColor(COLORS["gridSmall"]), 1))
        step = 48
        for x in range(0, width, step):
            painter.drawLine(x, 0, x, height)
        for y in range(0, height, step):
            painter.drawLine(0, y, width, y)
        scale_x = width / 1440.0
        scale_y = height / 900.0
        reveal = min(1.0, self.progress * 1.35)
        settle = max(0.0, (self.progress - 0.75) / 0.25)
        for index, trace in enumerate(TRACES):
            points = [QPointF(x * scale_x, y * scale_y) for x, y in trace]
            total = len(points) - 1
            visible = reveal * total
            painter.setPen(QPen(QColor(95, 219, 196, int(72 * (1 - settle * 0.45))),
                                1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i in range(total):
                if visible <= i:
                    break
                fraction = min(1.0, visible - i)
                a = points[i]
                b = points[i + 1]
                end = QPointF(a.x() + (b.x() - a.x()) * fraction,
                              a.y() + (b.y() - a.y()) * fraction)
                painter.drawLine(a, end)
            painter.setBrush(QColor(COLORS["selected"]))
            painter.setPen(Qt.PenStyle.NoPen)
            for i, point in enumerate(points):
                if visible >= i and i % 2 == 0:
                    radius = 2.4 + (0.8 if (i + index) % 3 == 0 else 0)
                    painter.drawEllipse(point, radius, radius)
        # quiet vignette at the bottom
        painter.setPen(Qt.PenStyle.NoPen)
        painter.end()


class WelcomePage(QWidget):
    get_started = Signal()
    continue_as = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.reduced_motion = False
        self.resume_profile: dict | None = None
        self.canvas = WelcomeCanvas(self)
        self.canvas.lower()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        brand_row = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(mota_icon().pixmap(26, 26))
        brand_row.addWidget(icon_label)
        brand = QLabel("MoTA")
        brand.setObjectName("Brand")
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        layout.addLayout(brand_row)
        layout.addStretch(3)
        self.wordmark = QLabel("MoTA")
        self.wordmark.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.wordmark.setStyleSheet("font-size: 108px; font-weight: 700; letter-spacing: 2px;")
        layout.addWidget(self.wordmark)
        self.tagline = QLabel("Turn an idea into a board.")
        self.tagline.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.tagline.setStyleSheet(f"color: {COLORS['textSecondary']}; font-size: 18px;")
        layout.addWidget(self.tagline)
        layout.addSpacing(24)
        self.cta = QPushButton("Get started")
        self.cta.setObjectName("Primary")
        self.cta.setMinimumWidth(168)
        self.cta.clicked.connect(self.get_started.emit)
        button_row = QVBoxLayout()
        button_row.setSpacing(10)
        button_row.addWidget(self.cta, 0, Qt.AlignmentFlag.AlignHCenter)
        self.continue_button = QPushButton("Continue as …")
        self.continue_button.setVisible(False)
        self.continue_button.clicked.connect(self.continue_as.emit)
        button_row.addWidget(self.continue_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(button_row)
        layout.addStretch(4)
        footer = QHBoxLayout()
        version = QLabel(f"Mota {__version__} · Local workspace")
        version.setObjectName("Muted")
        footer.addWidget(version)
        footer.addStretch(1)
        self.badge = QLabel("Native desktop · Mota circuit engine")
        self.badge.setObjectName("Muted")
        footer.addWidget(self.badge)
        layout.addLayout(footer)
        self._animations: list[QPropertyAnimation] = []
        self._prepare()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self.canvas.setGeometry(self.rect())
        super().resizeEvent(event)

    def _prepare(self) -> None:
        self.canvas.resize(self.size())
        effect = QGraphicsOpacityEffect(self.wordmark)
        self.wordmark.setGraphicsEffect(effect)
        effect.setOpacity(0.0)
        self.wordmark_effect = effect
        for widget in (self.tagline, self.cta):
            fx = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(fx)
            fx.setOpacity(0.0)
            if widget is self.tagline:
                self.tagline_effect = fx
            else:
                self.cta_effect = fx

    def set_resume(self, profile: dict | None) -> None:
        self.resume_profile = profile
        if profile:
            self.continue_button.setText(f"Continue as {profile.get('display_name') or profile.get('username')}")
            self.continue_button.setVisible(True)
        else:
            self.continue_button.setVisible(False)

    def set_reduced_motion(self, reduced: bool) -> None:
        self.reduced_motion = reduced
        self.canvas.reduced_motion = reduced
        if reduced:
            self.canvas.stop()
            self.wordmark_effect.setOpacity(1.0)
            self.tagline_effect.setOpacity(1.0)
            self.cta_effect.setOpacity(1.0)

    def start(self) -> None:
        for animation in self._animations:
            animation.stop()
        self._animations.clear()
        self.canvas.start()
        if self.reduced_motion:
            self.wordmark_effect.setOpacity(1.0)
            self.tagline_effect.setOpacity(1.0)
            self.cta_effect.setOpacity(1.0)
            return
        start_pos = self.wordmark.pos()
        move = QPropertyAnimation(self.wordmark, b"pos", self)
        move.setDuration(700)
        move.setStartValue(QPoint(start_pos.x() - 48, start_pos.y()))
        move.setEndValue(start_pos)
        move.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade = QPropertyAnimation(self.wordmark_effect, b"opacity", self)
        fade.setDuration(700)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        move.start()
        fade.start()
        self._animations.extend([move, fade])
        for effect, delay in ((self.tagline_effect, 200), (self.cta_effect, 350)):
            animation = QPropertyAnimation(effect, b"opacity", self)
            animation.setDuration(300)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            self._animations.append(animation)
            QTimer.singleShot(delay, animation.start)

    def stop_animations(self) -> None:
        self.canvas.stop()
        for animation in self._animations:
            animation.stop()

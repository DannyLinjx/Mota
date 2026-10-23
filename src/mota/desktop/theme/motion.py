"""Small, interruptible page transitions that never touch live OpenGL surfaces."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QLabel, QStackedWidget, QWidget,
)

DURATION_MS = 160


def fade_switch(stack: QStackedWidget, widget: QWidget, reduced: bool = False) -> None:
    """Switch pages with a short cross-fade of the outgoing frame.

    The outgoing page is captured into a snapshot label; the live widget (which may
    host a QOpenGLWidget) is never given a graphics effect.
    """
    previous = stack.currentWidget()
    if previous is widget:
        return
    snapshot = None
    if (previous is not None and not reduced and previous.isVisible()
            and not previous.property("noFadeSnapshot")):
        try:
            snapshot = previous.grab()
        except Exception:
            snapshot = None
    stack.setCurrentWidget(widget)
    if reduced or snapshot is None or snapshot.isNull():
        return
    overlay = QLabel(stack)
    overlay.setPixmap(snapshot)
    overlay.setGeometry(stack.rect())
    overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    overlay.show()
    overlay.raise_()
    effect = QGraphicsOpacityEffect(overlay)
    overlay.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", overlay)
    animation.setDuration(DURATION_MS)
    animation.setStartValue(1.0)
    animation.setEndValue(0.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.finished.connect(overlay.deleteLater)
    animation.start()

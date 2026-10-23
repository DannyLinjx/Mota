"""Application icon: registers the compiled qrc and validates a real pixmap."""

from __future__ import annotations

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget

import mota.desktop.mota_resources_rc  # noqa: F401  registers :/mota/app.png

RESOURCE = ":/mota/app.png"


def mota_icon() -> QIcon:
    icon = QIcon(RESOURCE)
    pixmap = icon.pixmap(64, 64)
    if icon.isNull() or pixmap.isNull() or pixmap.width() == 0:
        raise RuntimeError(f"MoTA icon resource is unreadable: {RESOURCE}")
    return icon


def apply_mota_icon(app: QApplication, window: QWidget | None = None) -> QIcon:
    icon = mota_icon()
    app.setWindowIcon(icon)
    if window is not None:
        window.setWindowIcon(icon)
    return icon

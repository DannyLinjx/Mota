"""Project card with a real saved-revision thumbnail."""

from __future__ import annotations

import base64
from typing import Any

from PySide6.QtCore import QByteArray, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget,
)

from mota.desktop.bridge import EngineBridge
from mota.desktop.theme.tokens import COLORS, STATUS_COLORS

try:
    from PySide6.QtSvg import QSvgRenderer
except Exception:  # pragma: no cover
    QSvgRenderer = None


class Thumbnail(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(116)
        self.pixmap: QPixmap | None = None
        self.placeholder = "No saved scene"

    def set_svg(self, data: bytes) -> None:
        if QSvgRenderer is None:
            return
        renderer = QSvgRenderer(QByteArray(data))
        if not renderer.isValid():
            return
        ratio = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        pixmap = QPixmap(int(self.width() * ratio) or 320, int(self.height() * ratio) or 116)
        pixmap.fill(QColor(COLORS["background"]))
        pixmap.setDevicePixelRatio(ratio)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, pixmap.width() / ratio,
                                        pixmap.height() / ratio))
        painter.end()
        self.pixmap = pixmap
        self.update()

    def set_placeholder(self, text: str) -> None:
        self.placeholder = text
        self.pixmap = None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS["background"]))
        if self.pixmap:
            painter.drawPixmap(self.rect(), self.pixmap)
        else:
            painter.setPen(QColor(COLORS["textMuted"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.placeholder)
        painter.setPen(QColor(COLORS["border"]))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()


class ProjectCard(QFrame):
    open_project = Signal(str)
    action = Signal(str, str)          # action, project_id

    def __init__(self, card: dict[str, Any], bridge: EngineBridge,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.card = card
        self.bridge = bridge
        self.setObjectName("Raised")
        self.setMinimumWidth(240)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        self.thumbnail = Thumbnail()
        layout.addWidget(self.thumbnail)
        name = QLabel(card["name"])
        name.setObjectName("SectionTitle")
        name.setWordWrap(True)
        layout.addWidget(name)
        revision = card.get("current_revision_id") or "no revision"
        number = revision.split("_")[-1].lstrip("0") if revision.startswith("rev_") else "—"
        meta = QLabel(f"Revision {number} · {card.get('component_count', 0)} parts\n"
                      f"{card.get('updated_at', '')[:16].replace('T', ' ')}")
        meta.setObjectName("Muted")
        layout.addWidget(meta)
        status_row = QHBoxLayout()
        review = card.get("review_status", "DRAFT")
        review_label = QLabel(review.replace("_", " ").title())
        review_label.setStyleSheet(
            f"color: {STATUS_COLORS.get(review, COLORS['textSecondary'])}; font-size: 11px;")
        status_row.addWidget(review_label)
        run_state = card.get("latest_run_state")
        if run_state:
            run_label = QLabel(run_state.replace("_", " ").title())
            run_label.setObjectName("Muted")
            status_row.addWidget(run_label)
        status_row.addStretch(1)
        layout.addLayout(status_row)
        buttons = QHBoxLayout()
        open_button = QPushButton("Open")
        open_button.setObjectName("Primary")
        open_button.clicked.connect(lambda: self.open_project.emit(card["project_id"]))
        more = QPushButton("•••")
        more.setObjectName("Tool")
        more.setFixedWidth(36)
        more.clicked.connect(self._menu)
        buttons.addWidget(open_button)
        buttons.addWidget(more)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self._load_thumbnail()

    def _load_thumbnail(self) -> None:
        revision = self.card.get("current_revision_id")
        artifact = self.card.get("thumbnail_artifact_id")
        if not revision:
            self.thumbnail.set_placeholder("No revisions yet")
            return
        if not artifact:
            artifact = f"artifact_thumb_{revision}"
        self.thumbnail.set_placeholder("Loading saved scene…")

        def done(result, error):
            try:
                from shiboken6 import isValid

                if not isValid(self):
                    return
            except Exception:
                pass
            if error or not result:
                self.thumbnail.set_placeholder("No saved scene")
                return
            try:
                data = base64.b64decode(result.get("data_base64", ""))
            except Exception:
                self.thumbnail.set_placeholder("Thumbnail unavailable")
                return
            if data.lstrip().startswith(b"<svg") or b"<svg" in data[:200]:
                self.thumbnail.set_svg(data)
            else:
                self.thumbnail.set_placeholder("Thumbnail unavailable")

        self.bridge.call("artifact.read", {
            "project_id": self.card["project_id"],
            "design_version": {"revision_id": revision},
            "artifact_id": artifact, "offset": 0, "length": 1024 * 1024}, done, 20000)

    def _menu(self) -> None:
        menu = QMenu(self)
        rename = menu.addAction("Rename")
        duplicate = menu.addAction("Duplicate")
        if self.card.get("archived"):
            archive = menu.addAction("Restore from archive")
        else:
            archive = menu.addAction("Archive")
        menu.addSeparator()
        delete = menu.addAction("Delete permanently")
        choice = menu.exec(self.cursor().pos())
        mapping = {rename: "rename", duplicate: "duplicate", archive: "archive",
                   delete: "delete"}
        if choice in mapping:
            self.action.emit(mapping[choice], self.card["project_id"])

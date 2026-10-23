"""Issues panel bound to one design version."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout, QWidget,
)

from mota.desktop.theme.tokens import COLORS


class IssuesPanel(QFrame):
    locate = Signal(str)
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setMaximumHeight(210)
        self.report: dict[str, Any] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        header = QHBoxLayout()
        self.title = QLabel("Issues")
        self.title.setObjectName("SectionTitle")
        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        close = QPushButton("×")
        close.setObjectName("Tool")
        close.setFixedWidth(28)
        close.clicked.connect(self.closed.emit)
        header.addWidget(self.title)
        header.addWidget(self.summary, 1)
        header.addWidget(close)
        layout.addLayout(header)
        self.list = QListWidget()
        self.list.itemClicked.connect(self._locate)
        layout.addWidget(self.list)

    def set_report(self, report: dict[str, Any] | None, read_only: bool = False) -> None:
        self.report = report
        self.list.clear()
        if not report:
            self.summary.setText("No checks for this version")
            return
        counts = report.get("counts") or {}
        if not counts:
            checks = report.get("checks", [])
            counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "NOT_CHECKED": 0}
            for check in checks:
                counts[check.get("status", "NOT_CHECKED")] = \
                    counts.get(check.get("status", "NOT_CHECKED"), 0) + 1
        self.summary.setText(
            f"{counts.get('PASS', 0)} passed · {counts.get('FAIL', 0)} failed · "
            f"{counts.get('WARN', 0)} warnings · {counts.get('NOT_CHECKED', 0)} not checked")
        checks = report.get("checks", [])
        order = {"FAIL": 0, "WARN": 1, "NOT_CHECKED": 2, "PASS": 3}
        for check in sorted(checks, key=lambda c: order.get(c.get("status"), 4)):
            status = check.get("status", "")
            text = (f"{status} · {check.get('title', check.get('check_id'))}\n"
                    f"{check.get('message', '')}")
            if not check.get("required_for_review", True):
                text += "\nnot required for review"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, check.get("object_ids") or [])
            color = {
                "PASS": COLORS["success"], "FAIL": COLORS["error"],
                "WARN": COLORS["warning"], "NOT_CHECKED": COLORS["textMuted"],
            }.get(status, COLORS["textPrimary"])
            item.setForeground(Qt.GlobalColor.white)
            item.setToolTip(check.get("message", ""))
            self.list.addItem(item)
            if status in ("FAIL", "WARN", "NOT_CHECKED"):
                item.setBackground(Qt.GlobalColor.transparent)
                item.setText(text)
        if read_only:
            self.title.setText("Issues · read-only revision")

    def _locate(self, item: QListWidgetItem) -> None:
        objects = item.data(Qt.ItemDataRole.UserRole) or []
        if objects:
            self.locate.emit(objects[0])

    def clear(self) -> None:
        self.report = None
        self.list.clear()
        self.summary.setText("")

"""Job panel bound to the current design version.

A finished job is labelled "Saved" only when its result belongs to the design
version that is current now. A late result for an older version is shown as
``STALE`` and offers a re-solve instead of pretending to be the latest save.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from mota.desktop.scene_adapter import (
    JOB_FAILED, JOB_RUNNING, JOB_STALE, JOB_SUCCEEDED, JobSnapshot,
    VersionedJobBoard,
)


class JobPanel(QWidget):
    resolve_requested = Signal(str)

    def __init__(self, board: VersionedJobBoard | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.board = board or VersionedJobBoard()
        self.title = QLabel("Jobs", self)
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.saved = QLabel("", self)
        self.saved.setObjectName("savedBadge")
        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(
            ["Job", "Kind", "Version", "State", "Detail"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 130)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 110)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        header = QHBoxLayout()
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.saved)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(header)
        layout.addWidget(self.table, 1)
        self._refresh()

    # ------------------------------------------------------------------ inputs
    def set_current_version(self, design_version_key: str | None) -> None:
        self.board.set_current(design_version_key)
        self._refresh()

    def submit(self, job_id: str, kind: str, design_version_key: str,
               message: str = "") -> JobSnapshot:
        job = self.board.submit(job_id, kind, design_version_key, message)
        self._refresh()
        return job

    def start(self, job_id: str) -> None:
        self.board.start(job_id)
        self._refresh()

    def accept(self, job_id: str, design_version_key: str, state: str, *,
               message: str = "", progress: float = 1.0) -> str:
        status = self.board.accept(job_id, design_version_key, state,
                                   message=message, progress=progress)
        self._refresh()
        return status

    def rows(self) -> list[JobSnapshot]:
        return self.board.rows()

    def row_states(self) -> dict[str, str]:
        return {row.job_id: row.state for row in self.board.rows()}

    def saved_count(self) -> int:
        return len(self.board.saved_jobs())

    # ------------------------------------------------------------------ render
    def _refresh(self) -> None:
        rows = self.board.rows()
        self.table.setRowCount(len(rows))
        for index, job in enumerate(rows):
            values = [job.job_id, job.kind, job.design_version_key,
                      self._state_text(job), job.message]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3:
                    item.setForeground(self._state_color(job))
                self.table.setItem(index, column, item)
            action = QPushButton("Re-solve" if job.stale or job.state == JOB_FAILED
                                 else "Open", self)
            action.setEnabled(job.stale or job.state in {JOB_FAILED, JOB_SUCCEEDED})
            action.clicked.connect(
                lambda _checked=False, job_id=job.job_id:
                self.resolve_requested.emit(job_id))
            self.table.setCellWidget(index, 4, action)
        self.saved.setText(
            f"{self.saved_count()} saved · {self.board.summary()['stale']} stale")
        self.saved.setStyleSheet(
            "color: #79B8F2;" if self.saved_count() else "color: #748895;")

    @staticmethod
    def _state_text(job: JobSnapshot) -> str:
        if job.stale:
            return "STALE · ignored"
        return {
            "QUEUED": "Queued", JOB_RUNNING: "Running",
            JOB_SUCCEEDED: "Saved", JOB_FAILED: "Failed",
            "CANCELLED": "Cancelled",
        }.get(job.state, job.state)

    @staticmethod
    def _state_color(job: JobSnapshot):
        from PySide6.QtGui import QColor

        if job.stale:
            return QColor("#F1C36D")
        if job.state == JOB_SUCCEEDED:
            return QColor("#8FD1A8")
        if job.state == JOB_FAILED:
            return QColor("#F07E8A")
        if job.state == JOB_RUNNING:
            return QColor("#79B8F2")
        return QColor("#748895")

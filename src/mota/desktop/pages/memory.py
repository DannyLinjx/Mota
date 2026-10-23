"""Three-layer Markdown memory viewer/editor with tombstones."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from mota.desktop.bridge import EngineBridge
from mota.desktop.controllers import SessionController
from mota.desktop.theme.tokens import COLORS

LAYERS = [("short_term", "Short-term"), ("long_term", "Long-term"),
          ("experience", "Experience")]


class MemoryPage(QWidget):
    def __init__(self, bridge: EngineBridge, session: SessionController,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.session = session
        self.layer = "long_term"
        self.project_id: str | None = None
        self.entries: list[dict[str, Any]] = []
        self.selected: dict[str, Any] | None = None
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("Memory")
        title.setObjectName("PageTitle")
        self.context = QLabel("")
        self.context.setObjectName("Muted")
        reload_button = QPushButton("Reload")
        reload_button.clicked.connect(self.reload)
        header.addWidget(title)
        header.addWidget(self.context, 1)
        header.addWidget(reload_button)
        layout.addLayout(header)
        note = QLabel("Markdown in your workspace is the authority. Project entries apply "
                      "only to that project; user entries apply across your projects. "
                      "Forget writes a tombstone in the same memory layer.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        body = QHBoxLayout()
        body.setSpacing(12)
        self.layer_list = QListWidget()
        self.layer_list.setFixedWidth(160)
        for key, label in LAYERS:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.layer_list.addItem(item)
        self.layer_list.setCurrentRow(1)
        self.layer_list.currentRowChanged.connect(self._layer_changed)
        body.addWidget(self.layer_list)
        self.entry_list = QListWidget()
        self.entry_list.setMinimumWidth(280)
        self.entry_list.currentRowChanged.connect(self._entry_selected)
        body.addWidget(self.entry_list, 2)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.meta = QLabel("")
        self.meta.setObjectName("Mono")
        self.meta.setWordWrap(True)
        right_layout.addWidget(self.meta)
        self.viewer = QTextBrowser()
        right_layout.addWidget(self.viewer, 1)
        self.editor = QPlainTextEdit()
        self.editor.hide()
        right_layout.addWidget(self.editor, 1)
        actions = QHBoxLayout()
        self.edit_button = QPushButton("Edit")
        self.edit_button.setObjectName("Primary")
        self.edit_button.clicked.connect(self._start_edit)
        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("Primary")
        self.save_button.hide()
        self.save_button.clicked.connect(self._save)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.hide()
        self.cancel_button.clicked.connect(self._cancel_edit)
        self.forget_button = QPushButton("Forget")
        self.forget_button.clicked.connect(self._forget)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        actions.addWidget(self.edit_button)
        actions.addWidget(self.save_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.forget_button)
        actions.addWidget(self.status, 1)
        right_layout.addLayout(actions)
        body.addWidget(right, 3)
        layout.addLayout(body, 1)

    def set_project(self, project_id: str | None) -> None:
        self.project_id = project_id
        self.context.setText(f"Project filter: {project_id}" if project_id
                             else "No current project · only user-scope entries are shown")
        if self.isVisible():
            self.reload()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.reload()

    def _layer_changed(self, row: int) -> None:
        item = self.layer_list.item(row)
        if item:
            self.layer = item.data(Qt.ItemDataRole.UserRole)
            self.reload()

    def reload(self) -> None:
        params: dict[str, Any] = {"layer": self.layer}
        if self.project_id:
            params["project_id"] = self.project_id
        if self.layer == "short_term" and self.session.session_id:
            params["session_id"] = self.session.session_id

        def done(result, error):
            if error:
                self.status.setText(error.get("message", "Could not load memory"))
                return
            self.entries = result.get("entries", [])
            self.entry_list.clear()
            for entry in self.entries:
                scope = ("Only this project" if entry["scope"] == "project"
                         else "Across my projects")
                item = QListWidgetItem(
                    f"{entry['summary'][:70]}\n"
                    f"{scope} · {entry['status']} · {entry.get('updated_at', '')[:16]}")
                item.setForeground(Qt.GlobalColor.white)
                self.entry_list.addItem(item)
            if not self.entries:
                self.entry_list.addItem("No entries in this layer")
                self._clear_detail()
            else:
                self.entry_list.setCurrentRow(0)
            self.status.setText(f"{len(self.entries)} entries")

        self.bridge.call("memory.list", params, done)

    def _entry_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.entries):
            self._clear_detail()
            return
        entry = self.entries[row]
        self.selected = entry
        self._render(entry)

    def _render(self, entry: dict[str, Any]) -> None:
        scope = "Only this project" if entry["scope"] == "project" else "Across my projects"
        meta = (f"{entry['entry_id']} · {entry['status']} · {scope}\n"
                f"project {entry.get('project_id') or '—'} · updated "
                f"{entry.get('updated_at', '')}\n{entry.get('relative_path', '')}")
        if entry.get("module_id"):
            meta += (f"\nmodule {entry['module_id']} v{entry.get('module_version')} · "
                     f"snapshot {entry.get('library_snapshot_id')}")
        if entry.get("evidence_run_id"):
            meta += (f"\nevidence run {entry['evidence_run_id']} · revision "
                     f"{entry.get('evidence_revision_id')}")
        self.meta.setText(meta)
        self.viewer.setMarkdown(entry.get("body") or entry.get("summary", ""))
        self.viewer.show()
        self.editor.hide()
        self.edit_button.show()
        self.save_button.hide()
        self.cancel_button.hide()
        self.forget_button.setEnabled(True)

    def _clear_detail(self) -> None:
        self.selected = None
        self.meta.setText("")
        self.viewer.setPlainText("")
        self.editor.hide()
        self.viewer.show()
        self.edit_button.hide()
        self.save_button.hide()
        self.cancel_button.hide()
        self.forget_button.setEnabled(False)

    def _start_edit(self) -> None:
        if not self.selected:
            return
        self.editor.setPlainText(self.selected.get("body", ""))
        self.viewer.hide()
        self.editor.show()
        self.edit_button.hide()
        self.save_button.show()
        self.cancel_button.show()
        self.editor.setFocus()

    def _cancel_edit(self) -> None:
        if self.selected:
            self._render(self.selected)

    def _save(self) -> None:
        if not self.selected:
            return
        entry = self.selected

        def done(result, error):
            if error:
                if error.get("code") == "MEMORY_CONFLICT":
                    self.status.setText("This memory changed outside Mota. "
                                        "Reload before saving.")
                else:
                    self.status.setText(error.get("message", "Save failed"))
                return
            self.status.setText("Saved")
            self.reload()

        self.bridge.call("memory.update", {
            "entry_id": entry["entry_id"],
            "content": self.editor.toPlainText(),
            "expected_hash": entry.get("hash", ""),
            "scope": entry.get("scope"),
            "project_id": entry.get("project_id"),
        }, done)

    def _forget(self) -> None:
        if not self.selected:
            return
        entry = self.selected
        answer = QMessageBox.question(
            self, "Forget memory",
            f"Forget “{entry['summary'][:60]}”? A tombstone is written and the fact "
            f"will not be re-extracted automatically.")
        if answer != QMessageBox.StandardButton.Yes:
            return

        def done(result, error):
            if error:
                self.status.setText(error.get("message", "Forget failed"))
                return
            self.status.setText(f"Forgotten · {result.get('tombstone_id')}")
            self.reload()

        self.bridge.call("memory.forget", {
            "entry_id": entry["entry_id"],
            "semantic_key": entry.get("semantic_key")}, done)

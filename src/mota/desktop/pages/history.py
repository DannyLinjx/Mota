"""History: real project cards, revisions and read-only inspection."""

from __future__ import annotations

import base64
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QStackedWidget, QTabWidget, QTextBrowser, QVBoxLayout,
    QWidget,
)

from mota.desktop.bridge import EngineBridge
from mota.desktop.controllers import ProjectController, SessionController
from mota.desktop.theme.tokens import COLORS, STATUS_COLORS
from mota.desktop.widgets.project_card import ProjectCard, Thumbnail


class HistoryDetail(QFrame):
    """Selected project preview with the same revision facts as the project card."""

    open_project = Signal(str)
    open_details = Signal(str)

    def __init__(self, bridge: EngineBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.card: dict[str, Any] | None = None
        self.setObjectName("Raised")
        self.setMinimumWidth(280)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        self.thumbnail = Thumbnail()
        layout.addWidget(self.thumbnail)
        self.name = QLabel("Select a project")
        self.name.setObjectName("SectionTitle")
        self.name.setWordWrap(True)
        layout.addWidget(self.name)
        self.meta = QLabel("")
        self.meta.setObjectName("Mono")
        self.meta.setWordWrap(True)
        layout.addWidget(self.meta)
        self.review = QLabel("")
        self.review.setWordWrap(True)
        layout.addWidget(self.review)
        self.note = QLabel("")
        self.note.setObjectName("Muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        layout.addStretch(1)
        buttons = QHBoxLayout()
        self.open_button = QPushButton("Open in Build")
        self.open_button.setObjectName("Primary")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open)
        self.details_button = QPushButton("Version details")
        self.details_button.setEnabled(False)
        self.details_button.clicked.connect(self._details)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.details_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def show_card(self, card: dict[str, Any] | None) -> None:
        previous = self.card
        same_scene = bool(
            card and previous and card["project_id"] == previous["project_id"]
            and card.get("current_revision_id") == previous.get("current_revision_id"))
        self.card = card
        if not card:
            self.thumbnail.set_placeholder("No project selected")
            self.name.setText("Select a project")
            self.meta.setText("")
            self.review.setText("")
            self.note.setText("List stays compact; details appear here.")
            self.open_button.setEnabled(False)
            self.details_button.setEnabled(False)
            return
        revision = card.get("current_revision_id") or ""
        number = revision.split("_")[-1].lstrip("0") if revision.startswith("rev_") else "—"
        self.name.setText(card["name"])
        self.meta.setText(
            f"Revision {number}\n{card.get('component_count', 0)} parts · "
            f"updated {card.get('updated_at', '')[:16].replace('T', ' ')}")
        status = card.get("review_status", "DRAFT")
        self.review.setText(f"{status.replace('_', ' ').title()}"
                            + (f" · {card['latest_run_state']}"
                               if card.get("latest_run_state") else ""))
        self.review.setStyleSheet(
            f"color: {STATUS_COLORS.get(status, COLORS['textSecondary'])}; font-weight: 600;")
        self.note.setText(
            "Opens the current revision in Build. Historic revisions stay read-only "
            "inside Version details." if revision else
            "This project has no saved revision yet. Open it to start a build.")
        self.open_button.setEnabled(True)
        self.details_button.setEnabled(True)
        if not same_scene or self.thumbnail.pixmap is None:
            self._load_thumbnail(card)

    def _load_thumbnail(self, card: dict[str, Any]) -> None:
        revision = card.get("current_revision_id")
        artifact = card.get("thumbnail_artifact_id")
        if not revision:
            self.thumbnail.set_placeholder("No revisions yet")
            return
        if not artifact:
            artifact = f"artifact_thumb_{revision}"
        self.thumbnail.set_placeholder("Loading saved scene…")
        project_id = card["project_id"]

        def done(result, error):
            if self.card is not card:
                return
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
            "project_id": project_id,
            "design_version": {"revision_id": revision},
            "artifact_id": artifact, "offset": 0, "length": 1024 * 1024}, done, 20000)

    def _open(self) -> None:
        if self.card:
            self.open_project.emit(self.card["project_id"])

    def _details(self) -> None:
        if self.card:
            self.open_details.emit(self.card["project_id"])



class ProjectDetailsDialog(QDialog):
    open_project = Signal(str, object)
    changed = Signal()

    def __init__(self, project_id: str, bridge: EngineBridge,
                 projects: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_id = project_id
        self.bridge = bridge
        self.projects = projects
        self.current_revision: str | None = None
        self.selected_revision: str | None = None
        self.revision_meta: dict[str, Any] | None = None
        self.setWindowTitle("Project details")
        self.setMinimumSize(820, 620)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title = QLabel("Project")
        self.title.setObjectName("PageTitle")
        open_button = QPushButton("Open in Build")
        open_button.setObjectName("Primary")
        open_button.clicked.connect(self._open_current)
        header.addWidget(self.title, 1)
        header.addWidget(open_button)
        layout.addLayout(header)
        self.banner = QLabel("")
        self.banner.setStyleSheet(f"color: {COLORS['warning']};")
        layout.addWidget(self.banner)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.overview = QTextBrowser()
        self.tabs.addTab(self.overview, "Overview")
        revisions_tab = QWidget()
        rev_layout = QHBoxLayout(revisions_tab)
        self.revisions = QListWidget()
        self.revisions.currentRowChanged.connect(self._revision_selected)
        rev_layout.addWidget(self.revisions, 2)
        rev_side = QVBoxLayout()
        self.view_button = QPushButton("View revision")
        self.view_button.setObjectName("Primary")
        self.view_button.clicked.connect(self._view_revision)
        self.restore_button = QPushButton("Restore as new revision")
        self.restore_button.clicked.connect(self._restore_revision)
        self.continue_button = QPushButton("Continue from here")
        self.continue_button.clicked.connect(self._continue_from)
        rev_side.addWidget(self.view_button)
        rev_side.addWidget(self.restore_button)
        rev_side.addWidget(self.continue_button)
        note = QLabel("Restore creates a new revision; history is never deleted. "
                      "Continue restores first, then opens the new baseline.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        rev_side.addWidget(note)
        rev_side.addStretch(1)
        rev_layout.addLayout(rev_side, 1)
        self.tabs.addTab(revisions_tab, "Revisions")
        self.checks = QListWidget()
        self.tabs.addTab(self.checks, "Checks")
        self.artifacts = QListWidget()
        self.tabs.addTab(self.artifacts, "Artifacts")
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        self.load()

    def load(self) -> None:
        def opened(result, error):
            if error:
                self.overview.setPlainText(error.get("message", "Project unavailable"))
                return
            self.title.setText(result["project"]["name"])
            self.current_revision = result["current_revision_id"]
            requirement = result.get("requirement") or {}
            parameters = requirement.get("parameters") or {}
            params = "\n".join(f"{k} = {v.get('value')} ({v.get('origin')})"
                               for k, v in parameters.items())
            checks = result.get("checks") or {}
            self.overview.setHtml(
                f"<h3>{result['project']['name']}</h3>"
                f"<p style='color:{COLORS['textSecondary']}'>"
                f"{requirement.get('purpose', '')}</p>"
                f"<p><b>Current</b> {self.current_revision}<br/>"
                f"<b>Review</b> {checks.get('review_status', '')}<br/>"
                f"<b>Template</b> {result['project'].get('template_id') or 'blank'}<br/>"
                f"<b>Components</b> {result.get('component_count', 0)} · "
                f"<b>Nets</b> {result.get('net_count', 0)}</p>"
                f"<p><b>Parameters</b><br/>{params or '—'}</p>")
            self.checks.clear()
            for check in checks.get("checks", []):
                item = QListWidgetItem(
                    f"{check.get('status')} · {check.get('title')} "
                    f"{'[required]' if check.get('required_for_review') else '[info]'}\n"
                    f"{check.get('message', '')}")
                item.setForeground(Qt.GlobalColor.white)
                self.checks.addItem(item)
            self._load_revisions()

        self.bridge.call("project.open", {"project_id": self.project_id}, opened, 30000)

    def _load_revisions(self) -> None:
        def done(result, error):
            self.revisions.clear()
            if error:
                return
            current = result.get("current_revision_id")
            for row in result.get("revisions", []):
                number = row["revision_id"].split("_")[-1].lstrip("0")
                marker = " · current" if row["revision_id"] == current else ""
                item = QListWidgetItem(
                    f"Revision {number} · {row['review_status']}{marker}\n"
                    f"{row['created_at']} · {row.get('summary', '')[:60]}")
                item.setData(Qt.ItemDataRole.UserRole, row["revision_id"])
                self.revisions.addItem(item)
            if self.revisions.count():
                self.revisions.setCurrentRow(self.revisions.count() - 1)

        self.bridge.call("revision.list", {"project_id": self.project_id}, done)

    def _revision_selected(self, row: int) -> None:
        if row < 0:
            return
        item = self.revisions.item(row)
        revision_id = item.data(Qt.ItemDataRole.UserRole)
        self.selected_revision = revision_id
        if revision_id == self.current_revision:
            self.banner.setText("")
        else:
            self.banner.setText(f"Viewing revision "
                                f"{revision_id.split('_')[-1].lstrip('0')} · "
                                f"Current is revision "
                                f"{(self.current_revision or '').split('_')[-1].lstrip('0')}"
                                f" · read-only")
        self._load_revision_detail(revision_id)

    def _load_revision_detail(self, revision_id: str) -> None:
        def done(result, error):
            if error:
                return
            self.revision_meta = result.get("revision")
            self.artifacts.clear()
            for artifact in (self.revision_meta or {}).get("artifacts", []):
                self.artifacts.addItem(f"{artifact.get('kind')} · "
                                       f"{artifact.get('relative_path')} · "
                                       f"{artifact.get('bytes')} bytes")
            self.checks.clear()
            report = result.get("checks") or {}
            for check in report.get("checks", []):
                self.checks.addItem(f"{check.get('status')} · {check.get('check_id')} · "
                                    f"{check.get('message', '')}")

        self.bridge.call("revision.open", {"project_id": self.project_id,
                                           "revision_id": revision_id}, done)

    def _view_revision(self) -> None:
        if self.selected_revision:
            self.open_project.emit(self.project_id, self.selected_revision)
            self.accept()

    def _open_current(self) -> None:
        self.open_project.emit(self.project_id, None)
        self.accept()

    def _restore_revision(self) -> None:
        if not self.selected_revision:
            return
        answer = QMessageBox.question(
            self, "Restore revision",
            "Restore will create a new revision from this snapshot. History is kept. Continue?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.projects.restore_revision(self.selected_revision,
                                       lambda result, error: self._restored(result, error))

    def _continue_from(self) -> None:
        if not self.selected_revision:
            return
        self.projects.restore_revision(
            self.selected_revision,
            lambda result, error: self._restored(result, error, open_after=True))

    def _restored(self, result, error, open_after: bool = False) -> None:
        if error:
            QMessageBox.warning(self, "Restore failed", error.get("message", ""))
            return
        self.changed.emit()
        if open_after and result:
            self.open_project.emit(self.project_id, result.get("revision_id"))
            self.accept()
        else:
            self.current_revision = (result or {}).get("revision_id")
            self.load()


class HistoryPage(QWidget):
    open_project = Signal(str, object)

    def __init__(self, bridge: EngineBridge, session: SessionController,
                 projects: ProjectController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.session = session
        self.projects = projects
        self.cards: list[dict[str, Any]] = []
        self.archived = False
        self._selected_project: str | None = None
        self._layout_signature: tuple | None = None
        self._build()
        self.projects.projects_loaded.connect(self._on_projects)
        self.projects.error.connect(
            lambda error: self.status.setText(error.get("message", "Could not load projects")))

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("Your projects")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        import_button = QPushButton("Import project")
        import_button.clicked.connect(self._import)
        new_button = QPushButton("New project")
        new_button.setObjectName("Primary")
        new_button.clicked.connect(self._new)
        header.addWidget(import_button)
        header.addWidget(new_button)
        layout.addLayout(header)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search projects")
        self.search.textChanged.connect(self._reflow)
        self.sort = QComboBox()
        self.sort.addItems(["Last updated", "Name"])
        self.sort.currentIndexChanged.connect(self._reflow)
        self.template_filter = QComboBox()
        self.template_filter.addItem("All templates", "")
        for template in ("power_3v3", "led_timer", "i2c_temperature"):
            self.template_filter.addItem(template, template)
        self.template_filter.currentIndexChanged.connect(self._reflow)
        self.active_button = QPushButton("Active")
        self.archived_button = QPushButton("Archived")
        for button in (self.active_button, self.archived_button):
            button.setCheckable(True)
        self.active_button.setChecked(True)
        self.active_button.clicked.connect(lambda: self._set_archived(False))
        self.archived_button.clicked.connect(lambda: self._set_archived(True))
        self.list_button = QPushButton("List")
        self.grid_button = QPushButton("Grid")
        for button in (self.list_button, self.grid_button):
            button.setCheckable(True)
            button.setObjectName("Tool")
        self.list_button.setChecked(True)
        self.list_button.clicked.connect(lambda: self._set_view("list"))
        self.grid_button.clicked.connect(lambda: self._set_view("grid"))
        filters.addWidget(self.search, 2)
        filters.addWidget(self.sort)
        filters.addWidget(self.template_filter)
        filters.addWidget(self.active_button)
        filters.addWidget(self.archived_button)
        filters.addSpacing(8)
        filters.addWidget(self.list_button)
        filters.addWidget(self.grid_button)
        layout.addLayout(filters)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        layout.addWidget(self.status)
        body = QHBoxLayout()
        body.setSpacing(14)
        self.views = QStackedWidget()
        self.project_list = QListWidget()
        self.project_list.setUniformItemSizes(True)
        self.project_list.setSpacing(2)
        self.project_list.currentItemChanged.connect(self._list_selected)
        self.views.addWidget(self.project_list)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(14)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.container)
        self.views.addWidget(self.scroll)
        empty_page = QWidget()
        empty_layout = QVBoxLayout(empty_page)
        self.empty = QLabel("No projects yet")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty)
        self.views.addWidget(empty_page)
        body.addWidget(self.views, 3)
        self.detail = HistoryDetail(self.bridge)
        self.detail.open_project.connect(lambda pid: self.open_project.emit(pid, None))
        self.detail.open_details.connect(self.open_details)
        body.addWidget(self.detail, 2)
        layout.addLayout(body, 1)
        self._view_mode = "list"

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        self.projects.list(archived=self.archived)

    def _set_archived(self, archived: bool) -> None:
        self.archived = archived
        self.active_button.setChecked(not archived)
        self.archived_button.setChecked(archived)
        self.refresh()

    def _on_projects(self, cards: list[dict[str, Any]]) -> None:
        self.cards = cards
        self._reflow()

    def _set_view(self, mode: str) -> None:
        self._view_mode = mode
        self.list_button.setChecked(mode == "list")
        self.grid_button.setChecked(mode == "grid")
        self._reflow()

    def _filtered(self) -> list[dict[str, Any]]:
        text = self.search.text().strip().casefold()
        template = self.template_filter.currentData()
        rows = [c for c in self.cards
                if (not text or text in c["name"].casefold())
                and (not template or c.get("template_id") == template)]
        if self.sort.currentIndex() == 1:
            rows.sort(key=lambda c: c["name"].casefold())
        else:
            rows.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        return rows

    def _fill_list(self, rows: list[dict[str, Any]]) -> None:
        self.project_list.blockSignals(True)
        self.project_list.clear()
        selected_row = 0
        for index, card in enumerate(rows):
            revision = card.get("current_revision_id") or ""
            number = (revision.split("_")[-1].lstrip("0")
                      if revision.startswith("rev_") else "—")
            review = (card.get("review_status") or "DRAFT").replace("_", " ").title()
            item = QListWidgetItem(
                f"{card['name']}\nRevision {number} · {review} · "
                f"{card.get('updated_at', '')[:16].replace('T', ' ')}")
            item.setSizeHint(QSize(0, 56))
            item.setData(Qt.ItemDataRole.UserRole, card)
            self.project_list.addItem(item)
            if card["project_id"] == self._selected_project:
                selected_row = index
        self.project_list.blockSignals(False)
        if rows:
            self.project_list.setCurrentRow(min(selected_row, len(rows) - 1))

    def _list_selected(self, current: QListWidgetItem | None, _previous) -> None:
        card = current.data(Qt.ItemDataRole.UserRole) if current else None
        if not card:
            return
        self._selected_project = card["project_id"]
        self.detail.show_card(card)

    def _reflow(self) -> None:
        rows = self._filtered()
        width = max(600, self.scroll.viewport().width())
        columns = 3 if width >= 1150 else 2 if width >= 760 else 1
        signature = (self._view_mode, columns, tuple(
            (c["project_id"], c.get("updated_at"), c.get("review_status")) for c in rows))
        if signature == getattr(self, "_layout_signature", None):
            return
        self._layout_signature = signature
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for index, card in enumerate(rows):
            widget = ProjectCard(card, self.bridge)
            widget.open_project.connect(lambda pid: self.open_project.emit(pid, None))
            widget.action.connect(self._card_action)
            self.grid.addWidget(widget, index // columns, index % columns)
        self._fill_list(rows)
        if not rows:
            self.empty.setText("No projects yet" if not self.cards
                               else "No projects match these filters")
            self.views.setCurrentIndex(2)
            self.detail.show_card(None)
            self.status.setText("")
            return
        self.status.setText(f"{len(rows)} project(s)")
        if self._view_mode == "grid":
            self.views.setCurrentWidget(self.scroll)
            self.detail.show_card(rows[0])
            self._selected_project = rows[0]["project_id"]
        else:
            self.views.setCurrentWidget(self.project_list)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reflow()

    def _card_action(self, action: str, project_id: str) -> None:
        if action == "rename":
            name, ok = QInputDialog.getText(self, "Rename project", "Name")
            if ok and name.strip():
                self.projects.rename(project_id, name.strip(),
                                     lambda result, error: self.refresh())
        elif action == "duplicate":
            self.projects.duplicate(project_id,
                                    lambda result, error: self.refresh())
        elif action == "archive":
            card = next((c for c in self.cards if c["project_id"] == project_id), None)
            if card and card.get("archived"):
                self.projects.restore_archive(project_id,
                                              lambda result, error: self.refresh())
            else:
                self.projects.archive(project_id, lambda result, error: self.refresh())
        elif action == "delete":
            card = next((c for c in self.cards if c["project_id"] == project_id), None)
            name = card["name"] if card else project_id
            answer = QMessageBox.question(
                self, "Delete permanently",
                f"Delete “{name}” and its project materials? Long-term preferences "
                f"are not deleted. This cannot be undone.")
            if answer == QMessageBox.StandardButton.Yes:
                self.projects.delete(project_id,
                                     lambda result, error: self.refresh())

    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "New project", "Project name")
        if ok and name.strip():
            self.projects.create(name.strip(),
                                 callback=lambda result: self.open_project.emit(
                                     result["project_id"], None))

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Mota project",
                                              "", "Mota bundle (*.mota);;All files (*)")
        if not path:
            return

        def done(result, error):
            if error:
                QMessageBox.warning(self, "Import failed", error.get("message", ""))
                return
            self.refresh()
            self.open_project.emit(result["project_id"], None)

        self.projects.import_bundle(path, done)

    def open_details(self, project_id: str) -> None:
        dialog = ProjectDetailsDialog(project_id, self.bridge, self.projects, self)
        dialog.open_project.connect(self.open_project)
        dialog.changed.connect(self.refresh)
        dialog.exec()
        self.refresh()

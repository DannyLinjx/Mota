"""Workspace shell: header, Build/History pages, account menu, status bar."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from mota.desktop.bridge import EngineBridge
from mota.desktop.controllers import (
    ConfigController, ProjectController, RunController, SelectionController,
    SessionController, TaskController,
)
from mota.desktop.pages.build import BuildPage
from mota.desktop.pages.history import HistoryPage, ProjectDetailsDialog
from mota.desktop.pages.memory import MemoryPage
from mota.desktop.pages.settings import SettingsPage
from mota.desktop.theme.motion import fade_switch
from mota.desktop.theme.tokens import COLORS
from mota.desktop.widgets.brand import BrandMark


class WorkspacePage(QWidget):
    sign_out = Signal()
    open_settings = Signal()
    open_memory = Signal()

    def __init__(self, bridge: EngineBridge, session: SessionController,
                 projects: ProjectController, runs: RunController,
                 config: ConfigController, tasks: TaskController,
                 selection: SelectionController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.session = session
        self.projects = projects
        self.runs = runs
        self.config_controller = config
        self.tasks = tasks
        self.selection = selection
        self.export_caps: list[dict[str, Any]] = []
        self.reduced_motion = False
        self.setProperty("noFadeSnapshot", True)
        self._build()
        self.bridge.engine_state.connect(self._engine_state)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = QFrame()
        self.header.setFixedHeight(64)
        self.header.setStyleSheet(f"background: {COLORS['shell']};"
                                  f"border-bottom: 1px solid {COLORS['border']};")
        header = QHBoxLayout(self.header)
        header.setContentsMargins(18, 8, 14, 8)
        header.setSpacing(10)
        header.addWidget(BrandMark(24))
        brand = QLabel("MOTA")
        brand.setObjectName("Brand")
        header.addWidget(brand)
        header.addSpacing(18)
        self.build_tab = QPushButton("Build")
        self.history_tab = QPushButton("History")
        for button in (self.build_tab, self.history_tab):
            button.setCheckable(True)
            button.setObjectName("Tab")
        self.build_tab.setChecked(True)
        self.build_tab.clicked.connect(lambda: self.show_page("build"))
        self.history_tab.clicked.connect(lambda: self.show_page("history"))
        header.addWidget(self.build_tab)
        header.addWidget(self.history_tab)
        header.addSpacing(12)
        project_box = QVBoxLayout()
        project_box.setSpacing(0)
        self.project_name = QLabel("No project")
        self.project_name.setStyleSheet("font-weight: 600;")
        self.project_meta = QLabel("")
        self.project_meta.setObjectName("Mono")
        project_box.addWidget(self.project_name)
        project_box.addWidget(self.project_meta)
        header.addLayout(project_box)
        self.save_state = QLabel("")
        self.save_state.setStyleSheet(f"color: {COLORS['textMuted']}; padding-left: 8px;")
        header.addWidget(self.save_state)
        header.addStretch(1)
        self.resources_button = QPushButton("Resources")
        self.resources_button.setObjectName("Tool")
        self.resources_button.setVisible(False)
        self.resources_button.setToolTip("Show components and templates")
        self.resources_button.clicked.connect(self._toggle_resources)
        header.addWidget(self.resources_button)
        self.agent_button = QPushButton("Agent")
        self.agent_button.setObjectName("Tool")
        self.agent_button.setVisible(False)
        self.agent_button.setToolTip("Show the Mota Agent panel and its input")
        header.addWidget(self.agent_button)
        new_button = QPushButton("New project")
        new_button.clicked.connect(self._new_project)
        header.addWidget(new_button)
        export_button = QPushButton("Export")
        export_button.clicked.connect(self._export_menu)
        header.addWidget(export_button)
        self.avatar = QPushButton("?")
        self.avatar.setFixedSize(34, 34)
        self.avatar.setStyleSheet(
            f"border-radius: 17px; background: {COLORS['panelRaised']};"
            f"border: 1px solid {COLORS['border']}; font-weight: 600;")
        self.avatar.clicked.connect(self._account_menu)
        header.addWidget(self.avatar)
        layout.addWidget(self.header)
        self.engine_banner = QFrame()
        self.engine_banner.setStyleSheet(
            f"background: {COLORS['panelRaised']}; border-bottom: 1px solid "
            f"{COLORS['error']};")
        banner_layout = QHBoxLayout(self.engine_banner)
        banner_layout.setContentsMargins(14, 6, 14, 6)
        self.engine_text = QLabel("The engine is not running.")
        self.engine_text.setStyleSheet(f"color: {COLORS['error']};")
        restart = QPushButton("Restart engine")
        restart.setObjectName("Tool")
        restart.clicked.connect(self.bridge.restart)
        banner_layout.addWidget(self.engine_text, 1)
        banner_layout.addWidget(restart)
        self.engine_banner.hide()
        layout.addWidget(self.engine_banner)

        self.stack = QStackedWidget()
        self.build = BuildPage(bridge=self.bridge, session=self.session,
                               projects=self.projects, runs=self.runs,
                               config=self.config_controller, tasks=self.tasks)
        self.build.setProperty("noFadeSnapshot", True)
        self.history = HistoryPage(self.bridge, self.session, self.projects)
        self.settings = SettingsPage(self.bridge, self.session,
                                     self.config_controller, self.tasks)
        self.memory = MemoryPage(self.bridge, self.session)
        self.stack.addWidget(self.build)
        self.stack.addWidget(self.history)
        self.stack.addWidget(self.settings)
        self.stack.addWidget(self.memory)
        layout.addWidget(self.stack, 1)

        self.status = QFrame()
        self.status.setFixedHeight(24)
        self.status.setStyleSheet(f"background: {COLORS['shell']};"
                                  f"border-top: 1px solid {COLORS['border']};")
        status_layout = QHBoxLayout(self.status)
        status_layout.setContentsMargins(12, 0, 12, 0)
        status_layout.setSpacing(16)
        self.status_save = QLabel("")
        self.status_coords = QLabel("X — · Y — · Snap 0.25 mm · 100%")
        self.status_coords.setObjectName("Mono")
        self.status_catalog = QLabel("Catalog: loading…")
        self.status_catalog.setObjectName("Muted")
        self.problems_button = QPushButton("Problems 0")
        self.problems_button.setObjectName("Tool")
        self.problems_button.setToolTip("Open or close the Problems panel")
        self.problems_button.clicked.connect(self.build.design_view.toggle_issues)
        self.status_engine = QLabel("Engine starting…")
        self.status_engine.setObjectName("Muted")
        self.status_local = QLabel("Local workspace")
        self.status_local.setObjectName("Muted")
        for widget in (self.status_save, self.status_coords, self.status_catalog,
                       self.problems_button, self.status_engine, self.status_local):
            status_layout.addWidget(widget)
            if widget is not self.status_local:
                status_layout.addWidget(self._divider())
        status_layout.insertStretch(4, 1)
        layout.addWidget(self.status)

        self.build.project_meta_changed.connect(self._project_meta)
        self.build.open_settings.connect(self.open_settings.emit)
        self.build.compact_mode_changed.connect(self._compact_changed)
        self.build.layout_mode_changed.connect(self._layout_mode_changed)
        self.agent_button.clicked.connect(self.build.toggle_agent)
        self.build.design_view.cursor_moved.connect(self._cursor)
        self.build.design_view.zoom_changed.connect(self._zoom)
        self.build.design_view.counts_changed.connect(self._problem_counts)
        self.build.selection_changed.connect(self._selection_changed)
        self.build.toast.connect(self._toast)
        self.projects.project_opened.connect(self._capture_capabilities)
        self.history.open_project.connect(self._from_history)
        self.settings.config_changed.connect(self._config_changed)
        self._refresh_catalog_status()

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFixedWidth(1)
        line.setFixedHeight(12)
        line.setStyleSheet(f"background: {COLORS['border']};")
        return line

    # ------------------------------------------------------------------ header
    def set_profile(self, profile: dict[str, Any] | None) -> None:
        if not profile:
            self.avatar.setText("?")
            self.project_name.setText("No project")
            self.project_meta.setText("")
            return
        name = profile.get("display_name") or profile.get("username") or "?"
        self.avatar.setText(name[:1].upper())
        self.avatar.setToolTip(name)
        self.build.resources.refresh()

    def _account_menu(self) -> None:
        menu = QMenu(self)
        settings = menu.addAction("AI connections")
        preferences = menu.addAction("Preferences")
        memory = menu.addAction("Memory")
        menu.addSeparator()
        signout = menu.addAction("Sign out")
        choice = menu.exec(self.avatar.mapToGlobal(self.avatar.rect().bottomLeft()))
        if choice is settings:
            self.show_page("settings")
            self.settings.load()
        elif choice is preferences:
            self.show_page("settings")
            self.settings.load()
        elif choice is memory:
            self.show_page("memory")
            self.open_memory.emit()
        elif choice is signout:
            self.sign_out.emit()

    def show_page(self, page: str) -> None:
        mapping = {"build": self.build, "history": self.history,
                   "settings": self.settings, "memory": self.memory}
        widget = mapping.get(page)
        if widget is None:
            return
        fade_switch(self.stack, widget, self.reduced_motion)
        self.build_tab.setChecked(page == "build")
        self.history_tab.setChecked(page == "history")
        if page == "history":
            self.history.refresh()
        elif page == "settings":
            self.settings.load()
        elif page == "memory":
            self.memory.set_project(self.build.active_project_id)
            self.memory.reload()
        elif page == "build":
            self.projects.list()
            self.build.load_templates()

    def _new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "New project", "Project name")
        if ok and name.strip():
            def created(result, error):
                if error:
                    QMessageBox.warning(self, "Could not create project",
                                        error.get("message", ""))
                    return
                self.show_page("build")
                self.build.open_project(result["project_id"])

            self.projects.create(name.strip(), callback=created)

    def _close_project(self) -> None:
        if not self.build.active_project_id:
            self._toast("没有打开的工程")
            return
        self.build.show_empty()
        self.project_name.setText("No project")
        self.project_meta.setText("")
        self.save_state.setText("")
        self.status_save.setText("")
        self.selection.select(None, None)
        self.memory.set_project(None)
        self._toast("工程已关闭 · 数据保留在 History")

    def _reset_layout(self) -> None:
        self.show_page("build")
        self.build.reset_layout()

    def _toggle_resources(self) -> None:
        self.build.toggle_resources()

    def _compact_changed(self, compact: bool) -> None:
        self.resources_button.setVisible(compact)

    def _layout_mode_changed(self, mode: str) -> None:
        self.resources_button.setVisible(mode != "wide")
        self.agent_button.setVisible(mode == "narrow")

    def _from_history(self, project_id: str, revision_id: object) -> None:
        self.show_page("build")
        self.build.open_project(project_id, revision_id if isinstance(revision_id, str)
                                else None)
        self.memory.set_project(project_id)

    def _selection_changed(self, kind: str, object_id: str) -> None:
        self.selection.select(kind or None, object_id or None)

    def _config_changed(self, config: dict[str, Any]) -> None:
        self.build.set_config(config)
        self.apply_reduced_motion(bool(config.get("reduced_motion")))
        self.build.agent.follow.setChecked(bool(config.get("follow_build", True)))
        self.build.design_view.view.set_units(config.get("units", "mm"))

    def apply_reduced_motion(self, reduced: bool) -> None:
        self.reduced_motion = reduced
        self.build.set_reduced_motion(reduced)

    # ------------------------------------------------------------------ status
    def _project_meta(self, meta: dict[str, Any]) -> None:
        if meta.get("project_id"):
            name = self.projects.card.get("name") if self.projects.card else ""
            self.project_name.setText(name or "Project")
            self.project_meta.setText(meta.get("revision_label", ""))
        state = meta.get("save_state") or ""
        color = {
            "success": COLORS["success"], "warning": COLORS["warning"],
            "draft": COLORS["draft"], "loading": COLORS["draft"],
        }.get(meta.get("save_kind"), COLORS["textMuted"])
        self.save_state.setText(state)
        self.save_state.setStyleSheet(f"color: {color}; padding-left: 8px;")
        self.status_save.setText(state)

    def _cursor(self, x: float, y: float) -> None:
        units = self.build.design_view.units_button.text()
        if units == "mm":
            value = f"X {x:.2f} mm · Y {y:.2f} mm"
        else:
            value = f"X {x / 25.4:.3f} in · Y {y / 25.4:.3f} in"
        self.status_coords.setText(f"{value} · Snap 0.25 mm")

    def _zoom(self, pixels_per_mm: float) -> None:
        zoom = pixels_per_mm / (96.0 / 25.4) * 100.0
        text = self.status_coords.text().split(" · Snap")[0]
        self.status_coords.setText(f"{text} · Snap 0.25 mm · {zoom:.0f}%")

    def _problem_counts(self, failures: int, warnings: int) -> None:
        self.problems_button.setText(f"Problems {failures + warnings}")
        color = (COLORS["error"] if failures else COLORS["warning"] if warnings
                 else COLORS["textMuted"])
        self.problems_button.setStyleSheet(f"color: {color};")

    def _refresh_catalog_status(self, attempt: int = 0) -> None:
        """Show the real component-library composition."""
        counts: dict[str, int] = {}

        def catalog_done(result, error):
            if error or not result:
                if attempt < 2:
                    QTimer.singleShot(1500, lambda: self._refresh_catalog_status(attempt + 1))
                else:
                    self.status_catalog.setText("Catalog unavailable")
                return
            library_counts = result.get("library_counts") or {}
            total = int(result.get("total") or 0)
            parts = [f"{total} assets"]
            for kind in ("component", "symbol", "footprint"):
                value = int(library_counts.get(kind) or 0)
                if value:
                    parts.append(f"{kind}s {value}")
            counts["catalog"] = len(parts)
            self.status_catalog.setText(
                "Catalog: " + " · ".join(parts) + self._template_suffix(counts))

        def template_done(result, error):
            if error or not result:
                return
            counts["templates"] = len(result.get("templates") or [])
            self.status_catalog.setText(self.status_catalog.text().split(" · templates")[0]
                                        + self._template_suffix(counts))

        self.build.bridge.call("catalog.search", {"query": "", "limit": 1}, catalog_done)
        self.build.bridge.call("template.list", {}, template_done)

    @staticmethod
    def _template_suffix(counts: dict[str, int]) -> str:
        if "templates" not in counts:
            return ""
        return f" · {counts['templates']} templates"

    def _engine_state(self, state: str, detail: str) -> None:
        if state == "down":
            self.engine_banner.show()
            self.engine_text.setText(detail or "The engine exited.")
            self.status_engine.setText("Engine down")
            self.status_engine.setStyleSheet(f"color: {COLORS['error']};")
        else:
            self.engine_banner.hide()
            self.status_engine.setText("Engine ready" if state == "ready"
                                       else "Engine starting…")
            self.status_engine.setStyleSheet(f"color: {COLORS['success']};")

    def _capture_capabilities(self, result: dict[str, Any]) -> None:
        caps = (result.get("capabilities") or {}).get("export") or {}
        self.export_caps = caps.get("formats", [])

    def _export_menu(self) -> None:
        if not self.build.active_project_id:
            QMessageBox.information(self, "Export", "Open a project first.")
            return
        if self.build.read_only:
            QMessageBox.information(self, "Export",
                                    "Read-only revisions can be exported, but choose a "
                                    "revision in History details. This build view exports "
                                    "the opened revision.")
        menu = QMenu(self)
        actions: dict[QAction, str] = {}
        formats = self.export_caps or [
            {"format": "mota", "label": "Mota project bundle", "available": True},
            {"format": "svg", "label": "Schematic and PCB SVG", "available": True},
            {"format": "bom", "label": "BOM CSV", "available": True},
            {"format": "report", "label": "Build report (Markdown)", "available": True},
            {"format": "kicad", "label": "KiCad files (compatibility unverified)",
             "available": False, "reason": "KiCad compatibility has not been verified."},
        ]
        for entry in formats:
            label = entry.get("label", entry["format"])
            action = menu.addAction(label)
            if not entry.get("available", True):
                action.setEnabled(False)
                action.setToolTip(entry.get("reason", ""))
            actions[action] = entry["format"]
        choice = menu.exec(self.cursor().pos())
        if choice not in actions:
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose export folder",
                                                  str(Path.home()))
        if not folder:
            return

        def done(result, error):
            if error:
                QMessageBox.warning(self, "Export failed",
                                    f"{error.get('code', '')}: "
                                    f"{error.get('message', '')}")
                return
            names = ", ".join(f["path"] for f in result.get("files", []))
            QMessageBox.information(self, "Export complete",
                                    f"Exported to:\n{names}")

        self.projects.export(actions[choice], folder, done)

    def _toast(self, text: str) -> None:
        self.status_save.setText(text)
        self.status_save.setStyleSheet(f"color: {COLORS['warning']};")

    # ------------------------------------------------------------------ layout
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        width = self.width()
        if width < 1240:
            self.build.set_compact(True)
        else:
            self.build.set_compact(False)
        compact = width < 980
        self.status_catalog.setVisible(not compact)
        self.status_local.setVisible(not compact)

    def clear_user_state(self) -> None:
        self.project_name.setText("No project")
        self.project_meta.setText("")
        self.save_state.setText("")
        self.status_save.setText("")
        self.build.show_empty()
        self.history.cards = []
        self.history._layout_signature = None
        self.history.detail.show_card(None)
        self.history._reflow()
        self.memory.project_id = None
        self.memory.entries = []
        self.memory.entry_list.clear()
        self.memory._clear_detail()

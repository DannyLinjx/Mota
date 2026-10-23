"""Native desktop entry point: window, page stack, menus and shortcuts."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QStackedWidget, QTextEdit, QVBoxLayout,
)

from mota.desktop.bridge import EngineBridge
from mota.desktop.controllers import (
    ConfigController, ProjectController, RunController, SelectionController,
    SessionController, TaskController,
)
from mota.desktop.icon import apply_mota_icon, mota_icon
from mota.desktop.pages.auth import AuthPage
from mota.desktop.pages.workspace import WorkspacePage
from mota.desktop.pages.welcome import WelcomePage
from mota.desktop.theme.motion import fade_switch
from mota.desktop.theme.tokens import COLORS, stylesheet


class MainWindow(QMainWindow):
    def __init__(self, root: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("MoTA")
        self.setWindowIcon(mota_icon())
        self.root = root
        self.settings = QSettings("Mota", "MotaDesktop")
        self.bridge = EngineBridge(root)
        self.session = SessionController(self.bridge)
        self.projects = ProjectController(self.bridge)
        self.runs = RunController(self.bridge)
        self.config_controller = ConfigController(self.bridge)
        self.tasks = TaskController(self.bridge)
        self.selection = SelectionController()
        self._restore_pending = False
        self._auth_mode = "login"
        self._auth_busy = False
        self._reduced_motion = False
        self._build_ui()
        self._wire()
        self._menus()
        self._shortcuts()
        self._restore_geometry()

    # ---------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        self.stack = QStackedWidget()
        self.welcome = WelcomePage()
        self.auth = AuthPage()
        self.workspace = WorkspacePage(
            self.bridge, self.session, self.projects, self.runs,
            self.config_controller, self.tasks, self.selection)
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.auth)
        self.stack.addWidget(self.workspace)
        self.setCentralWidget(self.stack)
        self.statusBar().hide()

    def _wire(self) -> None:
        self.bridge.notification.connect(self.runs.handle_event)
        self.bridge.task_event.connect(self.tasks.handle_event)
        self.welcome.get_started.connect(
            lambda: self._set_page(self.auth))
        self.welcome.continue_as.connect(self._continue_session)
        self.auth.back_requested.connect(
            lambda: self._set_page(self.welcome))
        self.auth.login_requested.connect(self._login)
        self.auth.register_requested.connect(self._register)
        self.session.auth_error.connect(self._auth_error)
        self.session.busy.connect(
            lambda busy: self.auth.set_busy(busy, self._auth_mode))
        self.session.session_changed.connect(self._session_changed)
        self.session.session_cleared.connect(self._session_cleared)
        self.workspace.sign_out.connect(self.session.logout)
        self.workspace.open_settings.connect(self._open_settings)
        self.workspace.open_memory.connect(self._open_memory)
        self.config_controller.config_loaded.connect(self._config_loaded)
        self.config_controller.error.connect(
            lambda error: self.workspace._toast(error.get("message", "")))
        self.workspace.settings.config_changed.connect(self._config_saved)
        self.projects.project_opened.connect(self._remember_project)
        self.workspace.build.project_meta_changed.connect(self._project_title)
        self.bridge.engine_state.connect(self._engine_state)
        self.workspace.history.open_project.connect(self.workspace._from_history)

    # ------------------------------------------------------------------ session
    def _login(self, username: str, password: str, remember: bool) -> None:
        if self._auth_busy:
            return
        self._auth_busy = True
        self._auth_mode = "login"
        self.auth.clear_error()
        self.auth.set_busy(True, "login")
        self.session.login(username, password, remember)

    def _register(self, username: str, password: str, display_name: str) -> None:
        if self._auth_busy:
            return
        self._auth_busy = True
        self._auth_mode = "register"
        self.auth.clear_error()
        self.auth.set_busy(True, "register")
        self.session.register(username, password, display_name)

    def _auth_error(self, message: str) -> None:
        self._auth_busy = False
        mode = self._auth_mode
        self.auth.set_busy(False, mode)
        self.auth.show_error(message, mode)

    def _session_changed(self, profile: dict[str, Any] | None) -> None:
        if not profile:
            return
        self._auth_busy = False
        self.runs.reset()
        self.projects._scene_callbacks.clear()
        self.workspace.set_profile(profile)
        self.workspace.clear_user_state()
        self.auth.reset()
        self._set_page(self.workspace)
        self.workspace.show_page("build")
        self._restore_pending = True
        self.config_controller.load()
        self.workspace.memory.set_project(None)

    def _session_cleared(self) -> None:
        self._auth_busy = False
        self.runs.reset()
        self.projects.card = None
        self.projects.current_revision_id = None
        self.projects.revision = None
        self.projects.checks = None
        self.workspace.clear_user_state()
        self.workspace.set_profile(None)
        self.auth.reset()
        self._set_page(self.auth)
        self.session.profile = None

    def _continue_session(self) -> None:
        self.session.try_resume(self._resume_result)

    def _resume_result(self, ok: bool) -> None:
        if ok:
            self._set_page(self.workspace)
        else:
            self._set_page(self.auth)
            self.auth.show_login_form()
            self._auth_error("The saved session expired. Sign in again.")

    def _set_page(self, widget) -> None:
        fade_switch(self.stack, widget, self._reduced_motion)

    def _config_loaded(self, config: dict[str, Any]) -> None:
        self.workspace.build.set_config(config)
        self._reduced_motion = bool(config.get("reduced_motion"))
        self.workspace.apply_reduced_motion(self._reduced_motion)
        self.welcome.set_reduced_motion(self._reduced_motion)
        self.auth.set_reduced_motion(self._reduced_motion)
        self.workspace.build.agent.follow.setChecked(bool(config.get("follow_build", True)))
        self.workspace.build.design_view.units_button.setText(config.get("units", "mm"))
        for canvas in (self.workspace.build.design_view.schematic,
                       self.workspace.build.design_view.pcb2d):
            canvas.set_units(config.get("units", "mm"))
        if self._restore_pending:
            self._restore_pending = False
            self._maybe_restore_project(config)

    def _config_saved(self, config: dict[str, Any]) -> None:
        self._config_loaded(config)

    def _maybe_restore_project(self, config: dict[str, Any]) -> None:
        if not config.get("restore_last_project", True):
            return
        last = self.settings.value("last_project", "")
        if not last:
            return

        def loaded(result, error):
            if error or not result:
                return
            ids = {card["project_id"] for card in result.get("projects", [])}
            if last in ids:
                self.workspace.build.open_project(last)
                self.workspace.memory.set_project(last)

        self.bridge.call("project.list", {"archived": False}, loaded)

    def _remember_project(self, result: dict[str, Any]) -> None:
        project = result.get("project") or {}
        project_id = project.get("project_id") or result.get("project_id")
        if project_id and not result.get("read_only"):
            self.settings.setValue("last_project", project_id)
            self.workspace.memory.set_project(project_id)

    def _project_title(self, meta: dict[str, Any]) -> None:
        if not meta.get("project_id"):
            self.setWindowTitle("MoTA")
            return
        card = self.projects.card or {}
        name = card.get("name") or "Project"
        self.setWindowTitle(f"{name} — MoTA")

    def _open_settings(self) -> None:
        self.workspace.show_page("settings")
        self.workspace.settings.load()

    def _open_memory(self) -> None:
        self.workspace.memory.set_project(self.workspace.build.active_project_id)
        self.workspace.show_page("memory")
        self.workspace.memory.reload()

    # ------------------------------------------------------------------- menus
    def _add_action(self, menu, label: str, handler, shortcut=None, *,
                    enabled: bool = True, reason: str = "") -> QAction:
        action = QAction(label, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(handler)
        action.setEnabled(enabled)
        if not enabled and reason:
            action.setToolTip(reason)
            action.setStatusTip(reason)
        menu.addAction(action)
        return action

    def _menus(self) -> None:
        menu = self.menuBar()
        build = self.workspace.build

        file_menu = menu.addMenu("File")
        self._add_action(file_menu, "New Blank Board…", build.new_blank_board,
                         QKeySequence.StandardKey.New)
        self._add_action(
            file_menu, "New Schematic Sheet…", lambda: None,
            QKeySequence("Ctrl+Shift+N"), enabled=False,
            reason="The current data model has one schematic sheet per board.")
        self._add_action(file_menu, "Open Project…",
                         lambda: self.workspace.show_page("history"),
                         QKeySequence.StandardKey.Open)
        self._add_action(file_menu, "Close Project", self.workspace._close_project)
        file_menu.addSeparator()
        self._add_action(file_menu, "Save", self._save,
                         QKeySequence.StandardKey.Save)
        self._add_action(file_menu, "Save As…", self._save_as,
                         QKeySequence("Ctrl+Shift+S"))
        self._add_action(file_menu, "Export…", self.workspace._export_menu,
                         QKeySequence("Ctrl+Shift+E"))
        file_menu.addSeparator()
        self._add_action(file_menu, "Sign out", self.session.logout)
        self._add_action(file_menu, "Quit MoTA", self.close,
                         QKeySequence.StandardKey.Quit)

        edit_menu = menu.addMenu("Edit")
        self._add_action(edit_menu, "Undo", self._undo,
                         QKeySequence.StandardKey.Undo)
        self._add_action(edit_menu, "Redo", self._redo,
                         QKeySequence.StandardKey.Redo)
        edit_menu.addSeparator()
        self._add_action(edit_menu, "Cut", lambda: self._text_action("cut"),
                         QKeySequence.StandardKey.Cut)
        self._add_action(edit_menu, "Copy", lambda: self._text_action("copy"),
                         QKeySequence.StandardKey.Copy)
        self._add_action(edit_menu, "Paste", lambda: self._text_action("paste"),
                         QKeySequence.StandardKey.Paste)
        self._add_action(edit_menu, "Delete", self._delete_selected,
                         QKeySequence.StandardKey.Delete)
        self._add_action(edit_menu, "Duplicate", lambda: None,
                         QKeySequence("Ctrl+D"), enabled=False,
                         reason="Duplicate needs a multi-object selection model.")
        edit_menu.addSeparator()
        self._add_action(edit_menu, "Select All", lambda: None,
                         QKeySequence.StandardKey.SelectAll, enabled=False,
                         reason="Only single-object selection is implemented.")
        self._add_action(edit_menu, "Deselect", self._deselect,
                         QKeySequence("Ctrl+Shift+A"))
        self._add_action(edit_menu, "Find…", self._focus_library,
                         QKeySequence.StandardKey.Find)
        self._add_action(edit_menu, "Properties", self._show_properties)

        view_menu = menu.addMenu("View")
        for label, view in (("Schematic", "schematic"),
                            ("PCB · 2D", "pcb2d"),
                            ("PCB · 3D", "pcb3d")):
            self._add_action(
                view_menu, label,
                lambda _=False, v=view: self._set_view(v))
        view_menu.addSeparator()
        self._add_action(view_menu, "Zoom In",
                         lambda: build.design_view._zoom(1.25),
                         QKeySequence.StandardKey.ZoomIn)
        self._add_action(view_menu, "Zoom Out",
                         lambda: build.design_view._zoom(0.8),
                         QKeySequence.StandardKey.ZoomOut)
        self._add_action(view_menu, "Fit Content", build.design_view.fit_content)
        self._add_action(view_menu, "Fit Page", build.design_view.fit_page)
        self._add_action(view_menu, "Fit Selection",
                         build.design_view.fit_selection)
        self._add_action(view_menu, "PCB Fit Board", build.design_view.fit_board)
        view_menu.addSeparator()
        self._add_action(view_menu, "Toggle Grid",
                         lambda: build.design_view.grid_button.toggle())
        self._add_action(view_menu, "Toggle Units",
                         lambda: build.design_view.units_button.click())
        self._add_action(view_menu, "Resources Panel", build.toggle_resources,
                         QKeySequence("Ctrl+Shift+R"))
        self._add_action(view_menu, "Agent Panel", build.toggle_agent,
                         QKeySequence("Ctrl+Shift+I"))
        self._add_action(view_menu, "Problems", build.design_view.toggle_issues,
                         QKeySequence("Ctrl+Shift+P"))
        self._add_action(view_menu, "Reset Layout", self.workspace._reset_layout)

        place_menu = menu.addMenu("Place")
        self._add_action(place_menu, "Component…", self._focus_library)
        self._add_action(place_menu, "Wire", lambda: None,
                         QKeySequence("W"), enabled=False,
                         reason="Schematic wire placement is not implemented yet.")
        self._add_action(place_menu, "Net Label", lambda: None, enabled=False,
                         reason="Net-label placement is not implemented yet.")
        self._add_action(place_menu, "Power Symbol", lambda: None, enabled=False,
                         reason="Power-symbol placement is not implemented yet.")
        self._add_action(place_menu, "No Connect", lambda: None, enabled=False,
                         reason="No-connect placement is not implemented yet.")
        self._add_action(place_menu, "Cancel Tool", self._escape)

        design_menu = menu.addMenu("Design")
        self._add_action(design_menu, "Run Electrical Check", self._run_checks,
                         QKeySequence("Ctrl+Shift+V"))
        self._add_action(design_menu, "PCB Check", self._run_checks)
        self._add_action(design_menu, "Project Settings",
                         self._show_properties)
        self._add_action(design_menu, "Assign Footprints…", lambda: None,
                         enabled=False,
                         reason="Footprint assignment needs a part browser flow.")

        tools_menu = menu.addMenu("Tools")
        self._add_action(tools_menu, "Stop Run", self._stop_run,
                         QKeySequence("Ctrl+."))
        self._add_action(tools_menu, "Component Library", self._focus_library)
        self._add_action(tools_menu, "Agent Settings", self._open_settings)
        self._add_action(tools_menu, "Preferences…", self._open_settings,
                         QKeySequence.StandardKey.Preferences)
        self._add_action(tools_menu, "Diagnostics…", self._diagnostics)

        help_menu = menu.addMenu("Help")
        self._add_action(help_menu, "Getting Started", self._getting_started)
        self._add_action(help_menu, "Keyboard Shortcuts", self._show_shortcuts,
                         QKeySequence("?"))
        self._add_action(help_menu, "Documentation", self._documentation)
        self._add_action(help_menu, "About MoTA", self._show_about)

    # ------------------------------------------------------------------ actions
    @staticmethod
    def _text_focus():
        widget = QApplication.focusWidget()
        return widget if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)) \
            else None

    def _undo(self) -> None:
        widget = self._text_focus()
        if widget is not None and hasattr(widget, "undo"):
            widget.undo()
            return
        self.workspace.build._undo()

    def _redo(self) -> None:
        widget = self._text_focus()
        if widget is not None and hasattr(widget, "redo"):
            widget.redo()
            return
        self.workspace.build._redo()

    def _text_action(self, name: str) -> None:
        widget = self._text_focus()
        if widget is not None and hasattr(widget, name):
            getattr(widget, name)()
            return
        self.workspace._toast("Clipboard actions apply to text fields only.")

    def _delete_selected(self) -> None:
        if self._text_focus() is not None:
            widget = self._text_focus()
            if hasattr(widget, "del_"):
                widget.del_()
            elif hasattr(widget, "cut"):
                widget.cut()
            return
        self.workspace.build.delete_selected()

    def _deselect(self) -> None:
        self.workspace.build.design_view.select(None, None)

    def _focus_library(self) -> None:
        build = self.workspace.build
        build.show_resources()
        build.resources.search.setFocus()

    def _run_checks(self) -> None:
        build = self.workspace.build
        if not build.active_project_id:
            self.workspace._toast("打开或新建工程后才能检查。")
            return
        self.projects.validation(
            lambda result: build.design_view.show_issues(), None)

    def _show_properties(self) -> None:
        build = self.workspace.build
        if not build.active_project_id:
            self.workspace._toast("尚未打开工程")
            return
        kind, object_id = build.selection()
        project = self.projects.card or {}
        lines = [f"Project: {project.get('name', '')}",
                 f"Persisted: {build.persisted_version or '—'}",
                 f"Displayed: {build.displayed_version or '—'}",
                 f"Scene: {build.scene_load_state}"]
        if kind:
            lines.append(f"Selection: {kind} {object_id}")
        QMessageBox.information(self, "Properties", "\n".join(lines))

    def _diagnostics(self) -> None:
        build = self.workspace.build
        QMessageBox.information(
            self, "Diagnostics",
            "\n".join([
                f"Engine: {'running' if self.bridge.is_running() else 'down'}",
                f"Project: {build.active_project_id or '—'}",
                f"Persisted version: {build.persisted_version or '—'}",
                f"Displayed version: {build.displayed_version or '—'}",
                f"Scene load: {build.scene_load_state}",
                f"Pending operation: {build.pending_operation_id or '—'}",
                f"Data root: {self.root or 'default'}",
            ]))

    def _getting_started(self) -> None:
        QMessageBox.information(
            self, "Getting started",
            "1. File → New Blank Board.\n"
            "2. Pick a part in the left Components tab and click Place.\n"
            "3. Click the canvas to place it; Esc cancels.\n"
            "4. Edits save as revisions immediately. Undo/Redo create a new revision.\n"
            "Templates are only used when you choose one in the Templates tab.")

    def _documentation(self) -> None:
        QMessageBox.information(
            self, "Documentation",
            "Owner's manual: docs/repair/canvas-audit.md\n"
            "Engine stages: docs/engine-v2/\n"
            "No network documentation is opened automatically.")

    def _save(self) -> None:
        self.workspace._toast(self.workspace.build.save_now())

    def _save_as(self) -> None:
        project_id = self.workspace.build.active_project_id
        if not project_id:
            self.workspace._toast("尚未打开工程")
            return

        def done(result, error):
            if error:
                self.workspace._toast(error.get("message", "Save As failed"))
                return
            new_id = result.get("project_id")
            self.workspace.build.open_project(new_id)
            self.workspace._toast("已创建独立工程副本")

        self.projects.duplicate(project_id, done)

    def _stop_run(self) -> None:
        if not self.workspace.build.runs.run_id:
            self.workspace._toast("没有正在运行的任务")
            return
        self.workspace.build._cancel_run()

    def _shortcuts(self) -> None:
        def bind(sequence: str, callback, widget=None, context=None) -> None:
            shortcut = QShortcut(QKeySequence(sequence), widget or self)
            shortcut.setContext(context or Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts_list.append(shortcut)

        self._shortcuts_list: list[QShortcut] = []
        bind("Ctrl+K", lambda: self.workspace.build.resources.search.setFocus())
        bind("Ctrl+L", lambda: self.workspace.build.agent.composer.setFocus())
        bind("Esc", self._escape)
        view = self.workspace.build.design_view
        for sequence, target in (
                ("F", view.fit_content), ("Shift+F", view.fit_page),
                ("A", self._focus_library),
                ("R", self.workspace.build.rotate_selected),
                ("G", lambda: view.grid_button.toggle()),
                ("+", lambda: view._zoom(1.25)),
                ("-", lambda: view._zoom(0.8)),
                ("1", lambda: self._set_view("schematic")),
                ("2", lambda: self._set_view("pcb2d")),
                ("3", lambda: self._set_view("pcb3d"))):
            shortcut = QShortcut(QKeySequence(sequence), view)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(target)
            self._shortcuts_list.append(shortcut)

    def _escape(self) -> None:
        self.workspace.build.design_view.set_tool("select")
        self.workspace.build.design_view.select(None, None)

    def _set_view(self, view: str) -> None:
        if self.stack.currentWidget() is self.workspace:
            self.workspace.show_page("build")
            self.workspace.build.design_view.set_view(view)

    def _show_shortcuts(self) -> None:
        QMessageBox.information(
            self, "Keyboard shortcuts",
            "New project  Ctrl/Cmd+N\nOpen History  Ctrl/Cmd+O\n"
            "Export  Ctrl/Cmd+Shift+E\nUndo / Redo  Ctrl/Cmd+Z / Ctrl/Cmd+Shift+Z\n"
            "Fit  F\nZoom  + / −\nCancel tool  Esc\nSearch parts  Ctrl/Cmd+K\n"
            "Focus Agent  Ctrl/Cmd+L\nSchematic / PCB 2D / PCB 3D  1 / 2 / 3")

    def _show_about(self) -> None:
        QMessageBox.about(
            self, "About Mota",
            "Mota 1.0.0\n\nA native desktop, CLI-driven agentic PCB workspace with a "
            "Mota-owned circuit and board engine. Design review is required before "
            "manufacturing; Mota does not certify production readiness.")

    # ------------------------------------------------------------------ engine
    def _engine_state(self, state: str, detail: str) -> None:
        if state == "down":
            self.workspace._engine_state(state, detail)

    # ------------------------------------------------------------------ window
    def _restore_geometry(self) -> None:
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        width, height = 1440, 900
        if available:
            width = min(width, max(1024, available.width() - 40))
            height = min(height, max(640, available.height() - 80))
        self.resize(QSize(width, height))
        saved = self.settings.value("window_geometry")
        if saved:
            self.restoreGeometry(saved)
        if available:
            geometry = self.frameGeometry()
            if not available.contains(geometry):
                self.move(available.x() + 20, available.y() + 20)
                self.resize(QSize(width, height))

    def closeEvent(self, event) -> None:  # noqa: N802
        self.settings.setValue("window_geometry", self.saveGeometry())
        self.bridge.shutdown()
        super().closeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not getattr(self, "_started", False):
            self._started = True
            self.welcome.start()
            self.session.try_resume(self._initial_resume)
            self.workspace._engine_state("starting", "Engine starting")

    def _initial_resume(self, ok: bool) -> None:
        if ok and self.session.profile:
            self.welcome.set_resume(self.session.profile)


def _set_process_name(name: str) -> None:
    """macOS 用进程名显示 Dock/菜单栏标签；裸 Python 启动时会显示 python3.12。"""
    if sys.platform != "darwin":
        return
    try:
        import ctypes

        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        send_address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
    except (OSError, AttributeError):
        return

    def send(restype, argtypes, receiver, selector, *args):
        prototype = ctypes.CFUNCTYPE(
            restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)
        return prototype(send_address)(
            receiver, objc.sel_registerName(selector), *args)

    try:
        text = send(ctypes.c_void_p, [ctypes.c_char_p],
                    objc.objc_getClass(b"NSString"),
                    b"stringWithUTF8String:", name.encode("utf-8"))
        info = send(ctypes.c_void_p, [],
                    objc.objc_getClass(b"NSProcessInfo"), b"processInfo")
        send(None, [ctypes.c_void_p], info, b"setProcessName:", text)
    except (ValueError, OSError):
        return


def _relaunch_as_mota() -> None:
    """macOS 的 Dock 标签取可执行文件名，裸 Python 启动时用同名链接重启一次。"""
    if sys.platform != "darwin":
        return
    if os.environ.get("MOTA_PROCESS_NAMED") == "1" or "pytest" in sys.modules:
        return
    if not sys.executable:
        return
    executable = Path(sys.executable)
    if executable.name == "MoTA":
        return
    link = executable.parent / "MoTA"
    try:
        if link.is_symlink():
            if Path(os.readlink(link)).resolve() != executable.resolve():
                link.unlink()
                link.symlink_to(executable)
        elif not link.exists():
            link.symlink_to(executable)
        else:
            return
    except OSError:
        return
    env = dict(os.environ)
    env["MOTA_PROCESS_NAMED"] = "1"
    paths = [entry for entry in sys.path if entry]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    os.execve(str(link), [str(link), "-c",
                          "from mota.desktop.main import run_desktop; "
                          "raise SystemExit(run_desktop())"], env)


def run_desktop(root: Path | None = None) -> int:
    if root is None:
        _relaunch_as_mota()
    _set_process_name("MoTA")
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Mota")
    app.setOrganizationName("Mota")
    app.setApplicationDisplayName("MoTA")
    apply_mota_icon(app)
    app.setStyleSheet(stylesheet())
    window = MainWindow(root)
    apply_mota_icon(app, window)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_desktop())

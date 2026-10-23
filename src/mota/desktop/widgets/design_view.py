"""Centre design view: tabs, toolbar, 2D/3D canvases and issues."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from mota.desktop.theme.tokens import COLORS
from mota.desktop.widgets.board_3d import Board3D
from mota.desktop.widgets.canvas_2d import Canvas2D
from mota.desktop.widgets.issues_panel import IssuesPanel


class DesignViewPanel(QWidget):
    selection_changed = Signal(str, str)
    move_requested = Signal(str, int, int)
    place_requested = Signal(float, float)
    undo_requested = Signal()
    redo_requested = Signal()
    cursor_moved = Signal(float, float)
    zoom_changed = Signal(float)
    status_message = Signal(str)
    view_changed = Signal(str)
    counts_changed = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.scenes: dict[str, dict[str, Any]] = {}
        self.current_view = "schematic"
        self.read_only = False
        self.run_active = False
        self._build()
        self._switch("schematic")

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        tabs = QFrame()
        tabs.setFixedHeight(40)
        tabs.setStyleSheet(f"background: {COLORS['shell']};"
                           f"border-bottom: 1px solid {COLORS['border']};")
        tabs_layout = QHBoxLayout(tabs)
        tabs_layout.setContentsMargins(8, 0, 8, 0)
        tabs_layout.setSpacing(2)
        self.tab_group = QButtonGroup(self)
        self.tabs: dict[str, QPushButton] = {}
        for index, (key, label) in enumerate((("schematic", "Schematic"),
                                              ("pcb2d", "PCB 2D"),
                                              ("pcb3d", "PCB 3D"))):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("Tab")
            button.clicked.connect(lambda _=False, k=key: self._switch(k))
            self.tab_group.addButton(button, index)
            self.tabs[key] = button
            tabs_layout.addWidget(button)
        tabs_layout.addStretch(1)
        self.version_label = QLabel("Revision 1")
        self.version_label.setObjectName("Mono")
        tabs_layout.addWidget(self.version_label)
        layout.addWidget(tabs)

        toolbar = QFrame()
        toolbar.setFixedHeight(36)
        toolbar.setStyleSheet(f"background: {COLORS['panel']};"
                              f"border-bottom: 1px solid {COLORS['border']};")
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(8, 2, 8, 2)
        bar.setSpacing(4)
        self.fit_button = self._tool_button("Fit", self.fit_content,
                                            "Fit content (F)")
        self.fit_page_button = self._tool_button("Page", self.fit_page,
                                                 "Fit page (Shift+F)")
        self.fit_selection_button = self._tool_button("Sel", self.fit_selection,
                                                      "Fit selection")
        self.zoom_in = self._tool_button("+", lambda: self._zoom(1.25), "Zoom in")
        self.zoom_out = self._tool_button("−", lambda: self._zoom(0.8), "Zoom out")
        self.select_button = self._tool_button("Select", lambda: self.set_tool("select"),
                                               "Select and move objects")
        self.measure_button = self._tool_button("Measure", lambda: self.set_tool("measure"),
                                                "Measure two points")
        self.grid_button = self._tool_button("Grid", self._toggle_grid, "Toggle grid")
        self.grid_button.setCheckable(True)
        self.grid_button.setChecked(True)
        self.units_button = self._tool_button("mm", self._toggle_units, "Toggle mm / in")
        self.undo_button = self._tool_button("Undo", self.undo_requested.emit, "Undo (Cmd+Z)")
        self.redo_button = self._tool_button("Redo", self.redo_requested.emit,
                                             "Redo (Cmd+Shift+Z)")
        self.top_button = self._tool_button("Top", lambda: self._view_3d("top"),
                                            "Top camera (does not change placement)")
        self.bottom_button = self._tool_button("Bottom", lambda: self._view_3d("bottom"),
                                               "Bottom camera")
        self.iso_button = self._tool_button("Isometric", lambda: self._view_3d("iso"),
                                            "Isometric camera")
        self.models_button = self._tool_button("Models", self._show_models,
                                               "Approximate / missing 3D models")
        for button in (self.fit_button, self.fit_page_button,
                       self.fit_selection_button, self.zoom_in, self.zoom_out,
                       self.select_button, self.measure_button, self.grid_button,
                       self.units_button, self.undo_button, self.redo_button,
                       self.top_button, self.bottom_button, self.iso_button,
                       self.models_button):
            bar.addWidget(button)
        bar.addStretch(1)
        self.tool_hint = QLabel("")
        self.tool_hint.setObjectName("Muted")
        bar.addWidget(self.tool_hint)
        layout.addWidget(toolbar)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(4)
        self.stack = QStackedWidget()
        self.schematic = Canvas2D()
        self.pcb2d = Canvas2D()
        # Schematic and PCB placement are separate: a schematic drag commits a
        # schematic-only symbol move and never touches the footprint.
        self.schematic.view.editable_move = True
        self.board3d = Board3D()
        self.board3d.request_2d.connect(lambda: self._switch("pcb2d"))
        self.stack.addWidget(self.schematic)
        self.stack.addWidget(self.pcb2d)
        self.stack.addWidget(self.board3d)
        splitter.addWidget(self.stack)
        self.issues = IssuesPanel()
        self.issues.hide()
        splitter.addWidget(self.issues)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        layout.addWidget(splitter, 1)
        for canvas in (self.schematic, self.pcb2d):
            canvas.object_selected.connect(self.selection_changed)
            canvas.move_requested.connect(self.move_requested)
            canvas.place_requested.connect(self.place_requested)
            canvas.cursor_moved.connect(self.cursor_moved)
            canvas.zoom_changed.connect(self.zoom_changed)
            canvas.status_message.connect(self.status_message)
        self.board3d.object_selected.connect(
            lambda cid: self.selection_changed("component" if cid else "", cid))
        self.board3d.status_message.connect(self.status_message)
        self.issues.locate.connect(self.locate_object)
        self.issues.closed.connect(self.issues.hide)
        self._dual_view_buttons = (self.select_button, self.measure_button,
                                   self.grid_button, self.undo_button, self.redo_button)

    def _tool_button(self, label: str, callback, tooltip: str) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName("Tool")
        button.setToolTip(tooltip)
        button.clicked.connect(callback)
        return button

    # ------------------------------------------------------------------- views
    def _switch(self, view: str) -> None:
        self.current_view = view
        self.tabs[view].setChecked(True)
        index = {"schematic": 0, "pcb2d": 1, "pcb3d": 2}[view]
        self.stack.setCurrentIndex(index)
        three_d = view == "pcb3d"
        for button in self._dual_view_buttons:
            button.setVisible(not three_d)
        for button in (self.top_button, self.bottom_button, self.iso_button):
            button.setVisible(three_d)
        self.models_button.setVisible(three_d)
        if view in self.scenes:
            self._apply_scene(view)
        self.tool_hint.setText("" if view != "pcb3d" else self.tool_hint.text())
        self.set_tool("select")
        self.view_changed.emit(view)

    def set_scene(self, view: str, dto: dict[str, Any]) -> None:
        self.scenes[view] = dto
        if view == self.current_view:
            self._apply_scene(view)

    def _apply_scene(self, view: str) -> None:
        dto = self.scenes.get(view)
        if not dto:
            return
        if view == "pcb3d":
            self.board3d.load_scene(dto)
            missing = dto.get("missing_models") or []
            self.models_button.setText(f"Models ({len(missing)})" if missing
                                       else "Models")
            if self.board3d.available:
                self.tool_hint.setText(
                    f"{len(missing)} approximate model(s)" if missing else "Exact models")
            else:
                self.tool_hint.setText("OpenGL unavailable")
            return
        canvas = self.schematic if view == "schematic" else self.pcb2d
        canvas.load_scene(dto)
        canvas.set_read_only(self.read_only)
        canvas.set_run_active(self.run_active)
        canvas.set_units(self.units_button.text())
        canvas.set_grid_visible(self.grid_button.isChecked())

    def set_view(self, view: str) -> None:
        self._switch(view)

    def set_version(self, kind: str, text: str) -> None:
        color = {
            "revision": COLORS["textSecondary"],
            "draft": COLORS["draft"],
            "readonly": COLORS["warning"],
            "loading": COLORS["draft"],
        }.get(kind, COLORS["textSecondary"])
        self.version_label.setText(text)
        self.version_label.setStyleSheet(f"color: {color};")

    def set_read_only(self, read_only: bool, active_run: bool = False) -> None:
        self.read_only = read_only
        self.run_active = active_run
        for canvas in (self.schematic, self.pcb2d):
            canvas.set_read_only(read_only)
            canvas.set_run_active(active_run)
        self.undo_button.setEnabled(not read_only and not active_run)
        self.redo_button.setEnabled(not read_only and not active_run)

    def set_report(self, report: dict[str, Any] | None, counts: dict[str, int] | None = None,
                   read_only: bool = False) -> None:
        if counts and report is not None:
            report = dict(report)
            report["counts"] = counts
        self.issues.set_report(report, read_only)
        counts = (report or {}).get("counts") or {}
        if report and not counts:
            counts = {"FAIL": 0, "WARN": 0}
            for check in report.get("checks", []):
                status = check.get("status")
                if status in counts:
                    counts[status] += 1
        self.counts_changed.emit(int(counts.get("FAIL", 0)), int(counts.get("WARN", 0)))

    def show_issues(self) -> None:
        self.issues.show()
        self.issues.raise_()

    def toggle_issues(self) -> None:
        if self.issues.isVisible():
            self.issues.hide()
        else:
            self.show_issues()

    def set_tool(self, tool: str) -> None:
        if tool == "select":
            self.select_button.setChecked(True)
            self.measure_button.setChecked(False)
        else:
            self.select_button.setChecked(False)
            self.measure_button.setChecked(True)
        for canvas in (self.schematic, self.pcb2d):
            canvas.set_tool(tool)

    def _zoom(self, factor: float) -> None:
        canvas = self.schematic if self.current_view == "schematic" else self.pcb2d
        if self.current_view == "pcb3d":
            self.board3d.fit() if factor < 1 else None
            self.board3d.view.opts["distance"] = max(
                10, self.board3d.view.opts.get("distance", 80) / factor)
            self.board3d.view.update()
            return
        canvas.zoom(factor)

    def begin_placement(self, label: str, width_mm: float = 4.0,
                        height_mm: float = 3.0) -> None:
        if self.current_view == "pcb3d":
            self._switch("pcb2d")
        canvas = self.schematic if self.current_view == "schematic" else self.pcb2d
        canvas.begin_placement(label, width_mm, height_mm)

    def cancel_placement(self) -> None:
        for canvas in (self.schematic, self.pcb2d):
            canvas.cancel_placement()

    def fit(self) -> None:
        self.fit_content()

    def fit_content(self) -> None:
        if self.current_view == "schematic":
            self.schematic.fit_content()
        elif self.current_view == "pcb2d":
            self.pcb2d.fit_content()
        else:
            self.board3d.fit()

    def fit_page(self) -> None:
        if self.current_view == "schematic":
            self.schematic.fit_page()
        elif self.current_view == "pcb2d":
            self.pcb2d.fit_page()
        else:
            self.board3d.fit()

    def fit_selection(self) -> None:
        if self.current_view == "schematic":
            self.schematic.fit_selection()
        elif self.current_view == "pcb2d":
            self.pcb2d.fit_selection()
        else:
            self.board3d.fit()

    def fit_board(self) -> None:
        if self.current_view == "pcb3d":
            self.board3d.fit()
            return
        if self.current_view != "pcb2d":
            self._switch("pcb2d")
        self.pcb2d.fit_board()

    def _toggle_grid(self) -> None:
        visible = self.grid_button.isChecked()
        for canvas in (self.schematic, self.pcb2d):
            canvas.set_grid_visible(visible)

    def _toggle_units(self) -> None:
        next_label = "in" if self.units_button.text() == "mm" else "mm"
        self.units_button.setText(next_label)
        for canvas in (self.schematic, self.pcb2d):
            canvas.set_units(next_label)

    def _view_3d(self, kind: str) -> None:
        self.board3d.set_view(kind)

    def select(self, kind: str | None, object_id: str | None) -> None:
        for canvas in (self.schematic, self.pcb2d):
            canvas.select(kind, object_id)
        if kind == "component":
            self.board3d.select_component(object_id)
        elif kind is None:
            self.board3d.select_component(None)

    def _show_models(self) -> None:
        models = (self.scenes.get("pcb3d") or {}).get("missing_models") or []
        dialog = QDialog(self)
        dialog.setWindowTitle("3D model fidelity")
        dialog.setMinimumWidth(460)
        layout = QVBoxLayout(dialog)
        note = QLabel("These components use simplified or missing 3D bodies. "
                      "Electrical and PCB data are unaffected.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        listing = QListWidget()
        for entry in models:
            item = QListWidgetItem(
                f"{entry.get('reference', entry.get('component_id'))} · "
                f"{entry.get('reason', '')}")
            item.setData(Qt.ItemDataRole.UserRole, entry.get("component_id"))
            listing.addItem(item)
        if not models:
            listing.addItem("All components have exact models")
        layout.addWidget(listing)
        row = QHBoxLayout()
        locate = QPushButton("Locate")
        locate.setObjectName("Primary")
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)

        def do_locate() -> None:
            item = listing.currentItem()
            component_id = item.data(Qt.ItemDataRole.UserRole) if item else None
            if component_id:
                self.selection_changed.emit("component", component_id)
                self.select("component", component_id)
                self.status_message.emit(f"Located {component_id} in 3D")
                dialog.accept()

        locate.clicked.connect(do_locate)
        listing.itemDoubleClicked.connect(lambda _: do_locate())
        row.addStretch(1)
        row.addWidget(locate)
        row.addWidget(close)
        layout.addLayout(row)
        dialog.exec()

    def locate_object(self, object_id: str) -> None:
        view = self.current_view
        dto = self.scenes.get("pcb2d" if view == "pcb3d" else view) or {}
        target = None
        for obj in dto.get("objects", []):
            if obj.get("component_id") == object_id or obj.get("id") == object_id:
                if obj.get("kind") == "polyline":
                    points = obj.get("points") or []
                    if points:
                        target = (sum(p[0] for p in points) / len(points),
                                  sum(p[1] for p in points) / len(points))
                        break
                elif obj.get("kind") in ("rect", "pad", "circle"):
                    target = (obj.get("x", 0) + (obj.get("w") or 0) / 2,
                              obj.get("y", 0) + (obj.get("h") or 0) / 2)
                    break
            if obj.get("net_id") == object_id and obj.get("kind") == "polyline":
                points = obj.get("points") or []
                if points:
                    target = (sum(p[0] for p in points) / len(points),
                              sum(p[1] for p in points) / len(points))
                    break
        if target:
            if view == "schematic":
                self.schematic.center_on_world(*target)
            elif view in ("pcb2d", "pcb3d"):
                self.pcb2d.center_on_world(*target)
            self.status_message.emit(f"Located {object_id}")

    def clear(self) -> None:
        self.cancel_placement()
        self.scenes.clear()
        self.issues.clear()
        self.issues.hide()
        self.set_version("revision", "")
        for canvas in (self.schematic, self.pcb2d):
            canvas.set_message("")
            canvas.select(None, None)
            canvas.reset_view()
        self.board3d.select_component(None)

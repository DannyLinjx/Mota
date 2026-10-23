"""Build workspace: resources, live design views and the real agent timeline."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDoubleValidator
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFormLayout, QFrame,
    QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSplitter, QStackedWidget,
    QVBoxLayout, QWidget,
)

from mota.desktop.bridge import EngineBridge
from mota.desktop.controllers import (
    ConfigController, ProjectController, RunController, SessionController, TaskController,
)
from mota.desktop.theme.tokens import COLORS
from mota.desktop.widgets.action_card import ErrorCard
from mota.desktop.widgets.agent_panel import AgentPanel
from mota.desktop.widgets.design_view import DesignViewPanel
from mota.desktop.widgets.resource_panel import ResourcePanel
from mota.circuit import parts
from mota.errors import MotaError, NotFound


class TemplateCard(QFrame):
    use = Signal(dict)
    preview = Signal(dict)

    def __init__(self, template: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.template = template
        self.setObjectName("Raised")
        self.setMinimumWidth(200)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        title = QLabel(template["title"])
        title.setObjectName("SectionTitle")
        title.setWordWrap(True)
        subtitle = QLabel(template.get("subtitle", ""))
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        board = template.get("board", {})
        meta = QLabel(f"{board.get('width_mm')}×{board.get('height_mm')} mm · "
                      f"{template.get('component_count')} parts · "
                      f"{template.get('net_count')} nets")
        meta.setObjectName("Mono")
        status = QLabel(template.get("status", "candidate").upper())
        color = COLORS["success"] if template.get("status") == "verified" else COLORS["warning"]
        status.setStyleSheet(f"color: {color}; font-size: 10px; letter-spacing: 1px;")
        row = QHBoxLayout()
        preview = QPushButton("Preview")
        preview.setObjectName("Tool")
        preview.setMinimumWidth(74)
        preview.clicked.connect(lambda: self.preview.emit(self.template))
        use = QPushButton("Use template")
        use.setObjectName("Primary")
        use.setMinimumWidth(118)
        use.clicked.connect(lambda: self.use.emit(self.template))
        row.addWidget(preview)
        row.addWidget(use)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(meta)
        layout.addWidget(status)
        layout.addStretch(1)
        layout.addLayout(row)


class EmptyState(QWidget):
    new_board = Signal()
    open_project = Signal()
    describe = Signal()
    import_requirements = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 40, 32, 32)
        layout.setSpacing(14)
        heading = QLabel("新建画板，或打开已有工程")
        heading.setObjectName("H2")
        layout.addWidget(heading)
        note = QLabel("画布为空。空白工程不会自动加载模板、示例器件或历史结果。"
                      "需要模板时从左侧 Templates 标签主动选择。")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        bottom = QHBoxLayout()
        new_button = QPushButton("New blank board")
        new_button.setObjectName("Primary")
        new_button.clicked.connect(self.new_board.emit)
        open_button = QPushButton("Open project")
        open_button.clicked.connect(self.open_project.emit)
        describe = QPushButton("Describe your idea")
        describe.clicked.connect(self.describe.emit)
        import_button = QPushButton("Import requirements")
        import_button.clicked.connect(self.import_requirements.emit)
        bottom.addWidget(new_button)
        bottom.addWidget(open_button)
        bottom.addWidget(describe)
        bottom.addWidget(import_button)
        bottom.addStretch(1)
        layout.addLayout(bottom)

    def set_templates(self, templates: list[dict[str, Any]]) -> None:
        """Compatibility: templates are listed in the left dock, not here."""

    def set_cards_enabled(self, enabled: bool) -> None:
        return


class TemplateDialog(QDialog):
    """Parameter preview. No project exists until Build this board is accepted."""

    def __init__(self, template: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.template = template
        self.output_parameters: dict[str, Any] = {}
        self.setWindowTitle(template["title"])
        self.setMinimumWidth(560)
        self.setMinimumHeight(480)
        self.resize(620, 700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        title = QLabel(template["title"])
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        description = QLabel(template.get("description", ""))
        description.setObjectName("Muted")
        description.setWordWrap(True)
        layout.addWidget(description)

        meta = QFrame()
        meta.setObjectName("Soft")
        meta_layout = QVBoxLayout(meta)
        meta_layout.setContentsMargins(12, 10, 12, 10)
        meta_layout.setSpacing(4)
        board = template.get("board", {})
        status = QLabel(
            f"Revision-ready recipe · status {template.get('status')} · "
            f"board {board.get('width_mm')} × {board.get('height_mm')} mm · "
            f"{template.get('component_count')} parts · {template.get('net_count')} nets")
        status.setObjectName("Mono")
        status.setWordWrap(True)
        meta_layout.addWidget(status)
        parts = ", ".join(p["reference"] for p in template.get("parts", []))
        parts_label = QLabel(f"Parts: {parts}")
        parts_label.setObjectName("Muted")
        parts_label.setWordWrap(True)
        meta_layout.addWidget(parts_label)
        layout.addWidget(meta)

        form_box = QFrame()
        form_box.setObjectName("Raised")
        form = QFormLayout(form_box)
        form.setContentsMargins(14, 14, 14, 14)
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.fields: dict[str, QLineEdit] = {}
        self._ranges: dict[str, tuple[float, float]] = {}
        defaults = template.get("defaults", {})
        constraints = template.get("constraints", {})
        units = {"input_voltage_v": "V", "supply_voltage_v": "V", "max_load_ma": "mA",
                 "target_frequency_hz": "Hz", "bus_frequency_khz": "kHz",
                 "board_width_mm": "mm", "board_height_mm": "mm",
                 "ra_kohm": "kΩ", "rb_kohm": "kΩ", "timing_cap_uf": "µF"}
        names = list(template.get("required_fields", []))
        for name in ("board_width_mm", "board_height_mm"):
            if name in defaults and name not in names:
                names.append(name)

        for name in names:
            if name not in defaults:
                continue
            rule = constraints.get(name, {})
            low = float(rule.get("min", 0.0)) if "min" in rule else 0.0
            high = float(rule.get("max", 1_000_000.0)) if "max" in rule else 1_000_000.0
            field = QLineEdit(f"{float(defaults[name]):g}")
            field.setAlignment(Qt.AlignmentFlag.AlignRight)
            field.setValidator(QDoubleValidator(low, high, 3, field))
            field.setMinimumHeight(38)
            if name in units:
                field.setPlaceholderText(units[name])
            self.fields[name] = field
            self._ranges[name] = (low, high)
            label = FIELD_LABELS.get(name, name.replace("_", " ").title())
            if name in units:
                label += f" ({units[name]})"
            tag = QLabel(label)
            tag.setObjectName("Muted")
            form.addRow(tag)
            form.addRow(field)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        content_layout.addWidget(form_box)
        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {COLORS['error']};")
        self.error.setWordWrap(True)
        content_layout.addWidget(self.error)
        note = QLabel("Build this board creates a new project and starts a run. "
                      "Cancelling leaves History unchanged.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        content_layout.addWidget(note)
        content_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        build = QPushButton("Build this board")
        build.setObjectName("Primary")
        build.clicked.connect(self._accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(build)
        layout.addLayout(buttons)

    def _accept(self) -> None:
        parameters: dict[str, float] = {}
        for name, field in self.fields.items():
            label = FIELD_LABELS.get(name, name.replace("_", " "))
            text = field.text().strip().replace(",", ".")
            try:
                value = float(text)
            except ValueError:
                self.error.setText(f"Enter a number for {label.lower()}.")
                field.setFocus()
                return
            low, high = self._ranges[name]
            if value < low or value > high:
                self.error.setText(f"{label} must be between {low:g} and {high:g}.")
                field.setFocus()
                return
            parameters[name] = value
        if parameters.get("board_width_mm", 40) < 20 or \
                parameters.get("board_height_mm", 30) < 20:
            self.error.setText("Board dimensions must be at least 20 mm.")
            return
        self.output_parameters = parameters
        self.accept()


FIELD_LABELS = {
    "input_voltage_v": "Input voltage",
    "supply_voltage_v": "Supply voltage",
    "max_load_ma": "Max load",
    "target_frequency_hz": "Target frequency",
    "bus_frequency_khz": "Bus frequency",
    "board_width_mm": "Board width",
    "board_height_mm": "Board height",
    "ra_kohm": "R A",
    "rb_kohm": "R B",
    "timing_cap_uf": "Timing capacitor",
}


def _parameter_summary(parameters: dict[str, Any]) -> str:
    return " · ".join(
        f"{FIELD_LABELS.get(name, name.replace('_', ' '))}={value:g}"
        for name, value in parameters.items())


class OverlayPanel(QFrame):
    """Floating resources/agent panel used when the splitter cannot fit them."""

    def __init__(self, parent: QWidget, title: str) -> None:
        super().__init__(parent)
        self.setObjectName("Overlay")
        self.setVisible(False)
        self._title = title
        self._content: QWidget | None = None
        self._reduced_motion = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)
        header = QHBoxLayout()
        self.title_label = QLabel(title)
        self.title_label.setObjectName("SectionTitle")
        close = QPushButton("×")
        close.setObjectName("Tool")
        close.setFixedWidth(28)
        close.clicked.connect(self.close_panel)
        header.addWidget(self.title_label, 1)
        header.addWidget(close)
        layout.addLayout(header)
        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.content_layout, 1)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(38)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 170))
        self.setGraphicsEffect(shadow)
        self._animation = QPropertyAnimation(self, b"geometry", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def set_content(self, widget: QWidget) -> None:
        if widget is self._content:
            return
        self._content = widget
        widget.setParent(self)
        self.content_layout.addWidget(widget)
        widget.show()

    def release_content(self) -> QWidget | None:
        widget = self._content
        self._content = None
        if widget is not None:
            self.content_layout.removeWidget(widget)
            widget.setParent(None)
        return widget

    def open_at(self, rect: QRect) -> None:
        self.setGeometry(rect)
        self.show()
        self.raise_()
        if self._reduced_motion:
            self._animation.stop()
            return
        start = QRect(rect.x() - 18, rect.y(), rect.width(), rect.height())
        self._animation.stop()
        self._animation.setStartValue(start)
        self._animation.setEndValue(rect)
        self._animation.start()

    def close_panel(self) -> None:
        self.hide()
        self._animation.stop()

    def set_reduced_motion(self, reduced: bool) -> None:
        self._reduced_motion = reduced
        self._animation.setDuration(0 if reduced else 150)


class BuildPage(QWidget):
    project_meta_changed = Signal(dict)
    open_settings = Signal()
    request_history = Signal()
    compact_mode_changed = Signal(bool)
    layout_mode_changed = Signal(str)
    toast = Signal(str)

    def __init__(self, bridge: EngineBridge, session: SessionController,
                 projects: ProjectController, runs: RunController,
                 config: ConfigController, tasks: TaskController,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.session = session
        self.projects = projects
        self.runs = runs
        self.config_controller = config
        self.tasks = tasks
        self.config: dict[str, Any] = {}
        self.templates: list[dict[str, Any]] = []
        self.active_project_id: str | None = None
        self.current_revision_id: str | None = None
        self.viewing_revision_id: str | None = None
        self.read_only = False
        self.scene_keys: dict[str, str] = {}
        self._tool_cards: dict[str, Any] = {}
        self._last_checks: dict[str, Any] | None = None
        self._pending_commit: dict[str, Any] | None = None
        self._pending_result_shown = False
        self._loading_final_revision: str | None = None
        self._pending_template: dict[str, Any] | None = None
        self._pending_parameters: dict[str, Any] | None = None
        self._pending_prompt: str | None = None
        self._pending_sources: list[str] = []
        self._pending_attach: str | None = None
        self._pending_revision: str | None = None
        self._run_mode = "template"
        self._last_user_text: str | None = None
        self._pending_place_part: str | None = None
        self._pending_blank = False
        self.persisted_version: str | None = None
        self.displayed_version: str | None = None
        self.displayed_target: dict[str, str] = {"view": "schematic"}
        self.pending_operation_id: str | None = None
        self.scene_load_state = "idle"
        self._last_change_summary = ""
        self.selected_kind: str = ""
        self.selected_id: str = ""
        self._compact = False
        self._layout_mode = "wide"
        self._reduced_motion = False
        self._build()

    # ------------------------------------------------------------------- build
    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(4)
        self.resources = ResourcePanel(self.bridge)
        self.center = QStackedWidget()
        self.empty = EmptyState()
        self.design_view = DesignViewPanel()
        self.center.addWidget(self.empty)
        self.center.addWidget(self.design_view)
        self.agent = AgentPanel()
        self.splitter.addWidget(self.resources)
        self.splitter.addWidget(self.center)
        self.splitter.addWidget(self.agent)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([252, 828, 352])
        layout.addWidget(self.splitter, 1)
        self.resources_overlay = OverlayPanel(self, "Components & templates")
        self.agent_overlay = OverlayPanel(self, "Mota Agent")
        self.resources_overlay.set_reduced_motion(self._reduced_motion)
        self.agent_overlay.set_reduced_motion(self._reduced_motion)

        self.empty.new_board.connect(self.new_blank_board)
        self.empty.open_project.connect(self.request_history.emit)
        self.empty.describe.connect(lambda: self.agent.composer.setFocus())
        self.empty.import_requirements.connect(self.attach_file)
        self.resources.template_use_requested.connect(self._confirm_template)
        self.resources.add_part_requested.connect(self._add_part)
        self.resources.ask_part_requested.connect(self._ask_part)
        self.agent.send_requested.connect(self.send_prompt)
        self.agent.attach_requested.connect(self.attach_file)
        self.agent.cancel_requested.connect(self._cancel_run)
        self.agent.answer_submitted.connect(self._answer)
        self.agent.open_settings.connect(self.open_settings.emit)
        self.agent.follow_changed.connect(self._set_follow)
        self.projects.project_opened.connect(self._on_project_opened)
        self.projects.project_changed.connect(self._on_project_changed)
        self.projects.error.connect(self._on_error)
        self.projects.revision_opened.connect(self._on_revision_opened)
        self.projects.checks_updated.connect(self._on_checks)
        self.runs.run_started.connect(self._on_run_started)
        self.runs.state_changed.connect(self._on_state)
        self.runs.run_event.connect(self._on_run_event)
        self.runs.question.connect(self._on_question)
        self.runs.committed.connect(self._on_committed)
        self.runs.run_finished.connect(self._on_run_finished)
        self.runs.error.connect(self._on_run_error)
        self.design_view.move_requested.connect(self._move_component)
        self.design_view.place_requested.connect(self._place_component)
        self.design_view.undo_requested.connect(self._undo)
        self.design_view.redo_requested.connect(self._redo)
        self.design_view.view_changed.connect(self._view_changed)
        self.design_view.selection_changed.connect(self._on_selection)
        self._watch_busy = False
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(1500)
        self._watch_timer.timeout.connect(self._watch_revision)
        self._watch_timer.start()
        # Object events from the running agent are the primary refresh driver;
        # they are coalesced here so one patch does not refetch a scene per
        # object. The 1.5 s revision poll above stays as an external-commit
        # safety net only.
        self._dirty_views: set[str] = set()
        self._dirty_timer = QTimer(self)
        self._dirty_timer.setSingleShot(True)
        self._dirty_timer.setInterval(120)
        self._dirty_timer.timeout.connect(self._flush_dirty_views)
        self.load_templates()

    def _mark_scene_dirty(self, view: str) -> None:
        if not self.runs.follow_build:
            return
        self._dirty_views.add(view)
        if not self._dirty_timer.isActive():
            self._dirty_timer.start()

    def _flush_dirty_views(self) -> None:
        views, self._dirty_views = self._dirty_views, set()
        for view in ("schematic", "pcb2d"):
            if view in views:
                self._request_draft_scene(view)

    # ----------------------------------------------------------------- catalogs
    def _watch_revision(self) -> None:
        """Follow commits made by another real editor/CLI/tool.

        Only identity changes trigger a reload; the canvas is never redrawn for
        an unchanged project, and active runs or pending edits win.
        """
        if not self.active_project_id or not self.current_revision_id:
            return
        if self._watch_busy or self._pending_commit or self.pending_operation_id:
            return
        if self.read_only:
            return
        if self.runs.state in ("CREATED", "RUNNING", "WAITING_FOR_USER",
                               "COMMITTING", "RENDERING"):
            return
        self._watch_busy = True

        def done(result: dict[str, Any] | None, error: dict[str, Any] | None) -> None:
            self._watch_busy = False
            if error or not result:
                return
            revision = result.get("current_revision_id")
            if not revision or revision == self.current_revision_id:
                return
            self.toast.emit(f"工程已更新 · {revision}")
            if self.active_project_id:
                self.projects.open(self.active_project_id)

        self.bridge.call("project.watch", {"project_id": self.active_project_id},
                         done, 8000)

    def load_templates(self) -> None:
        def done(result, error):
            if error:
                self.templates = []
                return
            self.templates = result.get("templates", [])
            self.empty.set_templates(self.templates)

        self.bridge.call("template.list", {}, done)

    def set_config(self, config: dict[str, Any]) -> None:
        self.config = config
        active = self.runs.run_id and self.runs.state not in (
            "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "RECOVERABLE")
        if active and self._run_mode == "template":
            self.agent.set_template_mode()
        else:
            self.agent.set_model(config.get("model", "openai"),
                                 bool(config.get("api_key_configured")),
                                 self._profile_label(config))
        if self.agent_overlay._content is self.agent:
            self._sync_overlay_title()

    def _sync_overlay_title(self) -> None:
        parts = self.agent.model_label.text().split(" · ")
        self.agent_overlay.title_label.setText(
            "Mota Agent · " + " · ".join(parts[:2]))

    @staticmethod
    def _profile_label(config: dict[str, Any]) -> str:
        default_id = config.get("default_profile_id")
        for profile in config.get("profiles") or []:
            if profile.get("profile_id") == default_id:
                return profile.get("name") or default_id or ""
        return ""

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self._apply_layout("compact" if compact else "wide")
        self.compact_mode_changed.emit(compact)

    def set_reduced_motion(self, reduced: bool) -> None:
        self._reduced_motion = reduced
        self.resources_overlay.set_reduced_motion(reduced)
        self.agent_overlay.set_reduced_motion(reduced)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._compact:
            self._apply_layout("wide")
        elif self.width() < 1200:
            self._apply_layout("narrow")
        else:
            self._apply_layout("compact")
        if self.resources_overlay.isVisible():
            self.resources_overlay.setGeometry(self._overlay_rect(360))
        if self.agent_overlay.isVisible():
            rect = self._overlay_rect(430)
            rect.moveLeft(max(12, self.width() - rect.width() - 12))
            self.agent_overlay.setGeometry(rect)

    # ------------------------------------------------------------ responsive
    def _apply_layout(self, mode: str) -> None:
        if mode == self._layout_mode:
            return
        self._layout_mode = mode
        if mode == "wide":
            self._release_overlay(self.resources_overlay, self.resources, 0)
            self._release_overlay(self.agent_overlay, self.agent, -1)
            self.splitter.setSizes([252, max(420, self.width() - 608), 352])
        elif mode == "compact":
            self._release_overlay(self.agent_overlay, self.agent, -1)
            self._move_to_overlay(self.resources_overlay, self.resources)
            self.resources_overlay.close_panel()
            self.splitter.setSizes([max(420, self.width() - 352), 352])
        else:
            self._release_overlay(self.agent_overlay, self.agent, -1)
            self._move_to_overlay(self.resources_overlay, self.resources)
            self._move_to_overlay(self.agent_overlay, self.agent)
            self.resources_overlay.close_panel()
            self.agent_overlay.close_panel()
            self.splitter.setSizes([max(320, self.width())])
        self.resources_overlay.set_reduced_motion(self._reduced_motion)
        self.agent_overlay.set_reduced_motion(self._reduced_motion)
        self.layout_mode_changed.emit(mode)

    def _move_to_overlay(self, overlay: OverlayPanel, widget: QWidget) -> None:
        if widget is overlay._content:
            return
        if self.splitter.indexOf(widget) >= 0:
            widget.setParent(None)
        overlay.set_content(widget)
        if widget is self.resources:
            self.resources.set_compact_density(True)
        elif widget is self.agent:
            self.agent.header.hide()
            self._sync_overlay_title()

    def _release_overlay(self, overlay: OverlayPanel, widget: QWidget,
                         index: int) -> None:
        if overlay._content is not widget:
            return
        overlay.release_content()
        if index < 0 or index > self.splitter.count():
            self.splitter.addWidget(widget)
        else:
            self.splitter.insertWidget(index, widget)
        widget.show()
        self.splitter.setStretchFactor(1, 1)
        if widget is self.resources:
            self.resources.set_compact_density(False)
        elif widget is self.agent:
            self.agent.header.show()

    def _overlay_rect(self, width: int) -> QRect:
        return QRect(12, 56, min(width, max(280, self.width() - 24)),
                     max(240, self.height() - 68))

    def toggle_resources(self) -> None:
        if self._layout_mode == "wide":
            self.resources.setVisible(not self.resources.isVisible())
            if self.resources.isVisible():
                self.resources.raise_()
            return
        if self.resources_overlay.isVisible():
            self.resources_overlay.close_panel()
        else:
            self.agent_overlay.close_panel()
            self._move_to_overlay(self.resources_overlay, self.resources)
            self.resources_overlay.open_at(self._overlay_rect(360))

    def show_resources(self) -> None:
        if self._layout_mode == "wide":
            if not self.resources.isVisible():
                self.resources.setVisible(True)
            self.resources.raise_()
            return
        if not self.resources_overlay.isVisible():
            self.agent_overlay.close_panel()
            self._move_to_overlay(self.resources_overlay, self.resources)
            self.resources_overlay.open_at(self._overlay_rect(360))

    def reset_layout(self) -> None:
        self.resources_overlay.close_panel()
        self.agent_overlay.close_panel()
        self._layout_mode = ""
        self._apply_layout("wide" if not self._compact else "compact")
        if self._layout_mode == "wide":
            total = max(900, self.width())
            self.splitter.setSizes([252, max(420, total - 604), 352])
            self.resources.setVisible(True)
            self.agent.setVisible(True)
        self.toast.emit("布局已重置")

    def toggle_agent(self) -> None:
        if self._layout_mode != "narrow":
            if self.agent.isVisible():
                self.agent.composer.setFocus()
            return
        if self.agent_overlay.isVisible():
            self.agent_overlay.close_panel()
        else:
            self.resources_overlay.close_panel()
            self._move_to_overlay(self.agent_overlay, self.agent)
            overlapping = self._overlay_rect(430)
            overlapping.moveLeft(max(12, self.width() - overlapping.width() - 12))
            self.agent_overlay.open_at(overlapping)
            self.agent.composer.setFocus()

    # --------------------------------------------------------------- templates
    def new_blank_board(self) -> None:
        if self.runs.run_id and self.runs.state not in (
                "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "RECOVERABLE"):
            self.toast.emit("当前任务尚未结束，先停止或等待完成。")
            return
        name, ok = QInputDialog.getText(self, "New blank board", "Board name",
                                        text="Untitled board")
        if not ok or not name.strip():
            return
        self._pending_blank = True
        self.agent.add_system_note("Creating a blank board. No template is used.")
        self.projects.create(name.strip())

    def _confirm_template(self, template: dict[str, Any]) -> None:
        if self.runs.run_id and self.runs.state not in (
                "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "RECOVERABLE"):
            self.toast.emit("当前任务尚未结束，先停止或等待完成。")
            return
        dialog = TemplateDialog(template, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._pending_template = template
        self._pending_parameters = dict(dialog.output_parameters)
        self._pending_prompt = None
        self.agent.add_user_message(f"Build {template['title']}")
        self.agent.add_system_note(
            "Template preview confirmed · " + _parameter_summary(self._pending_parameters))
        self.agent.begin_run("Creating project")
        self.projects.create(template["title"], template["template_id"])

    def _on_project_changed(self, result: dict[str, Any]) -> None:
        if self._pending_template is not None or self._pending_prompt is not None \
                or self._pending_attach is not None or self._pending_blank:
            self.active_project_id = result["project_id"]
            self.projects.open(result["project_id"])

    def _on_project_opened(self, result: dict[str, Any]) -> None:
        new_project_id = result["project"]["project_id"]
        switched = self.active_project_id != new_project_id
        self.active_project_id = new_project_id
        if switched:
            self.scene_keys.clear()
            self.design_view.scenes.clear()
            self.design_view.clear()
            self.agent.clear()
            self._tool_cards.clear()
            self._pending_commit = None
            self._pending_result_shown = False
            self._last_checks = None
        self.resources.refresh()
        self.current_revision_id = result["current_revision_id"]
        self.viewing_revision_id = result["current_revision_id"]
        self.persisted_version = self.current_revision_id
        self.displayed_version = None
        self.scene_load_state = "loading"
        self.pending_operation_id = None
        self._last_change_summary = ""
        self.read_only = False
        self.center.setCurrentWidget(self.design_view)
        self.design_view.set_read_only(False, bool(self.runs.run_id))
        self.resources.set_edit_enabled(True)
        self.design_view.set_report(result.get("checks"),
                                    result.get("checks_summary"))
        if result.get("checks_summary"):
            self._last_checks = {"review_status": result["revision"].get("review_status"),
                                 "counts": result.get("checks_summary"),
                                 "report": result.get("checks")}
        self._request_revision_scenes(self.current_revision_id, label_saved=True)
        self._emit_meta()
        active = result.get("active_run")
        if active:
            self.runs.run_id = active.get("run_id")
            self.runs.project_id = new_project_id
            self.runs.state = active.get("state", "CREATED")
            self.runs.draft_id = active.get("draft_id")
            self.runs.draft_seq = int(active.get("draft_seq") or 0)
            self.runs.inspect()
            self.design_view.set_read_only(False, True)
            self.agent.set_state(self.runs.state, {
                "RECOVERABLE": "Interrupted build found",
                "WAITING_FOR_USER": "Waiting for your input",
            }.get(self.runs.state, "Resuming build"))
        if self._pending_template is not None:
            template = self._pending_template
            self._pending_template = None
            self._start_template_run(template)
        elif self._pending_prompt is not None:
            text = self._pending_prompt
            self._pending_prompt = None
            self._start_prompt_run(text)
        elif self._pending_attach is not None:
            path = self._pending_attach
            self._pending_attach = None
            self._import_source(path)
        elif self._pending_revision:
            revision = self._pending_revision
            self._pending_revision = None
            self.projects.open_revision(revision)
        if self._pending_blank:
            self._pending_blank = False
            self.design_view.set_view("schematic")
            self.toast.emit("空白工程已保存 · 选择左侧器件后点击 Place")

    def _start_template_run(self, template: dict[str, Any]) -> None:
        self._run_mode = "template"
        parameters = dict(self._pending_parameters or template.get("defaults", {}))
        self._pending_parameters = None
        payload = {
            "kind": "template", "template_id": template["template_id"],
            "parameters": parameters,
            "accept_template_defaults": True,
        }
        self.agent.set_template_mode()
        self.runs.start(self.active_project_id or "", self.current_revision_id,
                        payload, lambda result, error: self._on_start_result(result, error))

    def _start_prompt_run(self, text: str) -> None:
        self._run_mode = "agent"
        payload = {"kind": "prompt", "text": text, "source_ids": list(self._pending_sources)}
        self._pending_sources = []
        self.agent.add_user_message(text)
        self.agent.begin_run("Starting build")
        self.runs.start(self.active_project_id or "", self.current_revision_id,
                        payload, lambda result, error: self._on_start_result(result, error))

    def _on_start_result(self, result, error) -> None:
        if error:
            self.agent.add_error_card(error.get("message", "Could not start the build"),
                                      error.get("code", ""),
                                      error.get("next_steps"))
            self.agent.finish()
            if error.get("code") == "MODEL_UNAVAILABLE":
                self.open_settings.emit()
            self.agent.set_send_enabled(not self.runs.run_id or True)

    # -------------------------------------------------------------------- prompts
    def send_prompt(self, text: str) -> None:
        if self.runs.run_id and self.runs.state not in (
                "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "RECOVERABLE"):
            self.toast.emit("当前任务尚未结束，先停止或等待完成。")
            self.agent.prefill(text)
            return
        if not self.config.get("api_key_configured"):
            self.agent.add_error_card(
                "Connect a model to build from a description. Template builds work "
                "without an API key.", "MODEL_UNAVAILABLE",
                ["Open model settings", "Choose a template card instead"])
            self.agent.prefill(text)
            self.open_settings.emit()
            return
        if self.read_only:
            self.toast.emit("Open the current revision before editing.")
            return
        if not self.active_project_id:
            self._pending_prompt = text
            name = text.strip().splitlines()[0][:48] or "New board"
            self.projects.create(name)
            return
        self._start_prompt_run(text)

    def attach_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import requirements", str(Path.home()),
            "Requirements (*.md *.txt *.pdf *.docx *.png *.jpg *.jpeg);;All files (*)")
        if not path:
            return
        if not self.active_project_id:
            self._pending_attach = path
            self.projects.create(Path(path).stem[:48] or "Requirements board")
            return
        self._import_source(path)

    def _import_source(self, path: str) -> None:
        def done(result, error):
            if error:
                self.agent.add_error_card(error.get("message", "Could not import the file"),
                                          error.get("code", ""))
                return
            status = result.get("status")
            note = (f"Attached {result.get('filename')} · "
                    f"{result.get('bytes', 0)} bytes · "
                    f"{result.get('section_count', 0)} sections")
            if result.get("pages"):
                note += f" · {result['pages']} pages"
            self.agent.add_system_note(note)
            for warning in result.get("warnings", []):
                self.agent.add_system_note("⚠ " + warning)
            self._pending_sources.append(result["source_id"])
            self.agent.prefill("Build this board from the attached requirements.")

        self.bridge.call("source.import", {
            "project_id": self.active_project_id, "file_path": path}, done, 60000)

    # -------------------------------------------------------------------- runs
    def _on_run_started(self, result: dict[str, Any]) -> None:
        self.agent.begin_run("Starting build")
        if self._run_mode == "template":
            self.agent.set_template_mode()
        else:
            binding = result.get("provider_binding") or {}
            if binding:
                self.agent.set_model(binding.get("model", ""), True,
                                     binding.get("profile_name", ""))
            if self.agent_overlay._content is self.agent:
                self._sync_overlay_title()
        self.design_view.set_read_only(False, True)
        self.resources.set_edit_enabled(False)
        self._tool_cards.clear()
        self._last_checks = None
        self._pending_commit = None
        self._pending_result_shown = False
        self._emit_meta()

    def _on_state(self, payload: dict[str, Any]) -> None:
        state = payload.get("state", "")
        label = payload.get("label", state)
        self.agent.set_state(state, label, payload.get("message", ""))
        self._loading_final_revision = None if state not in ("COMMITTING", "RENDERING") \
            else self._loading_final_revision
        if state in ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "RECOVERABLE"):
            self.design_view.set_read_only(self.read_only, False)
            self.resources.set_edit_enabled(not self.read_only)
        elif state == "COMMITTING":
            self.design_view.set_version("loading", "Saving revision…")
        self._emit_meta()

    def _on_run_event(self, event: dict[str, Any]) -> None:
        if event.get("project_id") != self.active_project_id:
            return
        event_type = event.get("type")
        payload = event.get("payload") or {}
        if event_type == "tool.started":
            card = self.agent.add_tool_card(payload.get("call_id", ""),
                                           payload.get("tool", ""),
                                           payload.get("arguments") or {})
            card.locate.connect(
                lambda object_id: self.design_view.select("component", object_id))
            card.open_checks.connect(self.design_view.show_issues)
            self._tool_cards[payload.get("call_id", "")] = card
        elif event_type == "tool.completed":
            card = self._tool_cards.get(payload.get("call_id", ""))
            if card:
                card.complete(payload.get("result") or {}, payload.get("duration_ms", 0))
        elif event_type == "tool.failed":
            card = self._tool_cards.get(payload.get("call_id", ""))
            if card:
                card.fail(payload.get("error") or {}, payload.get("retryable", False))
        elif event_type == "assistant.text_delta":
            self.agent.stream_text(payload.get("text", ""))
        elif event_type == "assistant.summary":
            existed = getattr(self.agent, "_stream_label", None) is not None
            self.agent.finish_stream(payload.get("text", "") if existed else "")
            if not existed and payload.get("text"):
                self.agent.add_assistant_summary(payload["text"])
        elif event_type == "design.patch_applied":
            self.design_view.set_version(
                "draft", f"Draft preview · seq {payload.get('draft_seq', 0)}")
            self._mark_scene_dirty("schematic")
            self._mark_scene_dirty("pcb2d")
        elif event_type in ("component.added", "net.connected", "footprint.assigned"):
            self._mark_scene_dirty("schematic")
        elif event_type == "board.track_created":
            self._mark_scene_dirty("pcb2d")
        elif event_type == "scene.updated":
            if not self.runs.follow_build:
                return
            version = payload.get("design_version") or {}
            if version.get("draft_id"):
                self.projects.scene_for_draft(
                    payload.get("view", "schematic"), version["draft_id"],
                    int(version.get("draft_seq", 0)),
                    lambda dto, key=payload.get("design_version_key"),
                    view=payload.get("view", "schematic"): self._apply_scene(view, dto, key))
        elif event_type == "validation.completed":
            report = {"checks": payload.get("checks", []),
                      "counts": payload.get("counts") or {},
                      "review_status": payload.get("review_status")}
            self._last_checks = {"review_status": payload.get("review_status"),
                                 "counts": payload.get("counts") or {},
                                 "report": report}
            self.design_view.set_report(report, payload.get("counts"), self.read_only)
            review = payload.get("review_status")
            self.project_meta_changed.emit({
                "save_state": ("Needs review" if review == "REVIEW_BLOCKED"
                               else "Ready for review" if review == "READY_FOR_REVIEW"
                               else "Draft"),
                "save_kind": "warning" if review != "READY_FOR_REVIEW" else "success",
            })
        elif event_type == "user_input.required":
            self.agent.set_state("WAITING_FOR_USER", "Waiting for your input")

    def _on_question(self, payload: dict[str, Any]) -> None:
        card = self.agent.add_clarification(payload)
        self.agent.set_state("WAITING_FOR_USER", "Waiting for your input")
        self._clarification = card

    def _answer(self, question_id: str, answer: dict[str, Any]) -> None:
        self.runs.answer(question_id, answer,
                         lambda result, error: self.agent.add_system_note(
                             "Answer accepted; the run continues." if not error
                             else f"Answer failed: {error.get('message', '')}"))

    def _cancel_run(self) -> None:
        self.agent.set_state(self.runs.state, "Cancelling…")
        self.runs.cancel(lambda result, error: self.agent.add_system_note(
            f"Cancel: {result.get('outcome') if result else error}"))

    def _on_committed(self, payload: dict[str, Any]) -> None:
        if payload.get("project_id") and payload["project_id"] != self.active_project_id:
            return  # A run keeps writing its own project; the open canvas is untouched.
        self._pending_commit = payload
        self._pending_result_shown = False
        self.current_revision_id = payload.get("revision_id")
        self.viewing_revision_id = payload.get("revision_id")
        self.persisted_version = payload.get("revision_id")
        self.scene_load_state = "loading"
        self.pending_operation_id = None
        self._last_change_summary = ""
        self.read_only = False
        self._loading_final_revision = payload.get("revision_id")
        self.design_view.set_version("loading", "Revision saved · Loading final view")
        self._request_revision_scenes(payload.get("revision_id"), label_saved=False)
        self._emit_meta()

    def _on_run_finished(self, payload: dict[str, Any]) -> None:
        if payload.get("project_id") and payload["project_id"] != self.active_project_id:
            return
        state = payload.get("state", "")
        self.agent.set_state(state, {
            "SUCCEEDED": "Build saved", "PARTIAL": "Build has unresolved items",
            "FAILED": "Build stopped", "CANCELLED": "Build cancelled",
            "RECOVERABLE": "Interrupted build found",
        }.get(state, state), payload.get("message", ""))
        self.agent.finish()
        self.design_view.set_read_only(False, False)
        self.resources.set_edit_enabled(True)
        if state == "FAILED":
            error = payload.get("error") or {}
            self.agent.add_error_card(error.get("message", "Build stopped"),
                                      error.get("code", ""),
                                      error.get("next_steps"))
            if self.active_project_id:
                self.projects.open(self.active_project_id)
        elif state == "CANCELLED":
            self.agent.add_system_note("The previous revision is unchanged. "
                                       "Inspect draft from the run if needed.")
        if self._pending_commit is not None:
            self._maybe_show_result()
        self._emit_meta()

    def _on_run_error(self, error: dict[str, Any]) -> None:
        self.agent.add_error_card(error.get("message", "Run failed"),
                                  error.get("code", ""), error.get("next_steps"))

    def _maybe_show_result(self) -> None:
        if self._pending_result_shown or not self._pending_commit:
            return
        revision = self._pending_commit.get("revision_id")
        current_key = self.scene_keys.get(self.design_view.current_view)
        if current_key != f"rev:{revision}":
            return
        checks = {
            "review_status": self._pending_commit.get("review_status")
            or (self._last_checks or {}).get("review_status", "DRAFT"),
            "counts": self._pending_commit.get("checks_summary")
            or (self._last_checks or {}).get("counts", {}),
        }
        card = self.agent.add_result_card(self._pending_commit, checks)
        card.open_checks.connect(self.design_view.show_issues)
        self._pending_result_shown = True
        self.design_view.set_report(
            (self._last_checks or {}).get("report") if self._last_checks else None,
            checks["counts"], self.read_only)
        self._loading_final_revision = None
        self._emit_meta()

    # ------------------------------------------------------------------- scenes
    def _request_revision_scenes(self, revision_id: str | None,
                                 label_saved: bool = True) -> None:
        if not revision_id:
            return
        if not label_saved:
            self._loading_final_revision = revision_id
        for view in ("schematic", "pcb2d", "pcb3d"):
            self.projects.scene(
                view, lambda dto, v=view, r=revision_id: self._apply_scene(
                    v, dto, f"rev:{r}"), revision_id)

    def _request_draft_scene(self, view: str) -> None:
        if not self.runs.draft_id:
            return
        self.projects.scene_for_draft(
            view, self.runs.draft_id, self.runs.draft_seq,
            lambda dto, v=view, seq=self.runs.draft_seq: self._apply_scene(
                v, dto, f"draft:{self.runs.draft_id}:{seq}"))

    def _apply_scene(self, view: str, dto: dict[str, Any],
                     expected_key: str | None = None) -> None:
        if not dto or "__error__" in dto:
            error = (dto or {}).get("__error__") or {}
            if view == self.design_view.current_view:
                self.scene_load_state = "failed"
                message = error.get("message", "Scene unavailable")
                (self.design_view.schematic if view == "schematic"
                 else self.design_view.pcb2d).set_message(message)
                self._emit_meta()
            return
        key = dto.get("design_version_key")
        if expected_key and key != expected_key:
            return
        if key and key.startswith("rev:"):
            revision = key.split(":", 1)[1]
            if revision != (self.viewing_revision_id or self.current_revision_id):
                return
        elif key and key.startswith("draft:"):
            parts = key.split(":")
            if len(parts) >= 3 and self.runs.draft_id and parts[1] != self.runs.draft_id:
                return
            if len(parts) >= 3 and int(parts[2]) != self.runs.draft_seq:
                return
        if key != self.scene_keys.get(view) or view == self.design_view.current_view:
            self.design_view.set_scene(view, dto)
        self.scene_keys[view] = key or ""
        if key and key.startswith("rev:"):
            revision = key.split(":", 1)[1]
            if view == self.design_view.current_view:
                number = revision.split("_")[-1].lstrip("0") or revision
                suffix = " · Read-only" if dto.get("read_only") else ""
                self.design_view.set_version(
                    "readonly" if dto.get("read_only") else "revision",
                    f"Revision {number}{suffix}")
                (self.design_view.schematic if view == "schematic"
                 else self.design_view.pcb2d).set_message("")
                self.displayed_version = revision
                self.displayed_target = {"view": view}
                self.scene_load_state = "idle"
            self._loading_final_revision = None
            self._maybe_show_result()
        elif key and key.startswith("draft:"):
            if view == self.design_view.current_view:
                seq = key.rsplit(":", 1)[-1]
                self.design_view.set_version("draft", f"Draft preview · seq {seq}")
        self._emit_meta()

    def _view_changed(self, view: str) -> None:
        key = self.scene_keys.get(view)
        if view == "pcb3d" and key:
            dto = self.design_view.scenes.get("pcb3d")
            if dto:
                return
        if key:
            if key.startswith("rev:"):
                number = key.split(":", 1)[1].split("_")[-1].lstrip("0")
                self.design_view.set_version("revision", f"Revision {number}")
            else:
                self.design_view.set_version("draft",
                                             f"Draft preview · seq {key.rsplit(':', 1)[-1]}")

    def _on_revision_opened(self, result: dict[str, Any]) -> None:
        self.read_only = bool(result.get("read_only"))
        self.viewing_revision_id = result["revision"]["revision_id"]
        self._pending_commit = None
        self._loading_final_revision = None
        self.design_view.set_read_only(self.read_only, bool(self.runs.run_id))
        self.design_view.set_report(result.get("checks"),
                                    result.get("checks", {}).get("counts"),
                                    self.read_only)
        self._request_revision_scenes(self.viewing_revision_id)
        self.center.setCurrentWidget(self.design_view)
        self.resources.set_edit_enabled(not self.read_only)
        self._emit_meta()

    def _on_checks(self, result: dict[str, Any]) -> None:
        self.design_view.set_report(result.get("report"), result.get("counts"),
                                    self.read_only)
        self._last_checks = {"review_status": (result.get("report") or {}).get(
            "review_status"), "counts": result.get("counts") or {},
            "report": result.get("report")}

    def _emit_meta(self) -> None:
        revision = (self.viewing_revision_id if self.read_only
                    else self.current_revision_id) or ""
        number = revision.split("_")[-1].lstrip("0") if revision else ""
        review = (self._last_checks or {}).get("review_status") or ""
        if self._loading_final_revision:
            state, kind = "Revision saved · Loading final view", "loading"
        elif self.runs.run_id and self.runs.state not in (
                "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "RECOVERABLE", "CREATED"):
            state, kind = f"Building · {self.runs.state.replace('_', ' ').title()}", "draft"
        elif self.read_only:
            current_number = (self.current_revision_id or "").split("_")[-1].lstrip("0")
            state, kind = f"Read-only · current is revision {current_number}", "warning"
        elif self.persisted_version and self.pending_operation_id:
            state, kind = "正在添加器件…", "loading"
        elif self.persisted_version and self.scene_load_state == "failed":
            state, kind = "设计已保存 · 画布更新失败 · 重试显示", "warning"
        elif self.persisted_version and self.displayed_version \
                and self.displayed_version != self.persisted_version:
            state, kind = "设计已保存 · 画布更新中", "loading"
        elif self.persisted_version and self.displayed_version is None:
            state, kind = "空白工程已保存 · 正在准备画布", "loading"
        elif self.displayed_version and self.displayed_version == self.persisted_version \
                and self._last_change_summary:
            state, kind = f"{self._last_change_summary} · 已保存并显示", "success"
        elif review == "READY_FOR_REVIEW":
            state, kind = "Saved · Ready for review", "success"
        elif review == "REVIEW_BLOCKED":
            state, kind = "Saved · Needs review", "warning"
        elif revision:
            state, kind = "Saved", "success"
        else:
            state, kind = "", "muted"
        self.project_meta_changed.emit({
            "project_id": self.active_project_id, "revision_id": revision,
            "revision_label": f"Revision {number}" + (" · Read-only" if self.read_only else "")
            if number else "",
            "save_state": state, "save_kind": kind, "read_only": self.read_only,
            "review_status": review})

    # ------------------------------------------------------------------- edits
    def _move_component(self, component_id: str, x_nm: int, y_nm: int) -> None:
        if self.read_only:
            self.toast.emit("该版本为只读，请在当前版本上编辑。")
            return
        if self._agent_busy():
            self.toast.emit("Agent 正在修改该工程，停止或等待任务结束后再编辑。")
            return
        if self.design_view.current_view == "schematic":
            self._patch([{"op": "move_schematic_symbol", "component_id": component_id,
                          "x_nm": x_nm, "y_nm": y_nm}],
                        f"Moved {component_id} in the schematic")
        else:
            self._patch([{"op": "move_component", "component_id": component_id,
                          "x_nm": x_nm, "y_nm": y_nm}], f"Moved {component_id}")

    def _add_part(self, part_id: str) -> None:
        """PREVIEW: arm placement; nothing is persisted until a canvas click."""

        if not self.active_project_id:
            self.toast.emit("请先新建空白画板或打开工程。")
            return
        if self.read_only:
            self.toast.emit("该版本为只读，请在当前版本上编辑。")
            return
        if self._agent_busy():
            self.toast.emit("Agent 正在修改该工程，停止或等待任务结束后再编辑。")
            return
        try:
            part = parts.resolve_part(part_id)
        except MotaError as exc:
            self.agent.add_error_card(exc.message, exc.code or "SYMBOL_UNAVAILABLE")
            return
        if not part.symbol_id:
            self.agent.add_error_card(
                f"{part.mpn} has no schematic symbol.", "SYMBOL_UNAVAILABLE")
            return
        width_mm, height_mm = 4.0, 3.0
        if part.footprint_id:
            try:
                footprint = parts.resolve_footprint(part.footprint_id)
                width_mm = max(0.6, footprint.courtyard.w_nm / 1e6)
                height_mm = max(0.6, footprint.courtyard.h_nm / 1e6)
            except NotFound:
                width_mm, height_mm = 4.0, 3.0
        self._pending_place_part = part_id
        self.design_view.begin_placement(part.mpn, width_mm, height_mm)
        self.toast.emit(f"Click the canvas to place {part.mpn} · Esc cancels")

    def _place_component(self, x_mm: float, y_mm: float) -> None:
        part_id = self._pending_place_part
        if not part_id:
            return
        self._pending_place_part = None
        self.toast.emit("Adding component…")
        self._patch(
            [{"op": "add_component", "part_id": part_id,
              "x_mm": round(x_mm, 3), "y_mm": round(y_mm, 3)}],
            "Added 1 component", operation_id=f"place_{uuid.uuid4().hex}",
            focus=(x_mm, y_mm))

    def _patch(self, operations: list[dict[str, Any]], note: str, *,
               operation_id: str | None = None,
               focus: tuple[float, float] | None = None) -> None:
        self.pending_operation_id = operation_id
        self.scene_load_state = "loading"

        def done(result, error):
            self.pending_operation_id = None
            if error:
                self.scene_load_state = "idle"
                code = error.get("code", "")
                self.agent.add_error_card(error.get("message", "Edit failed"),
                                          code, error.get("next_steps"))
                self.toast.emit(f"{code or 'Edit failed'}: "
                                f"{error.get('message', '')}")
                if self.current_revision_id:
                    # A rejected drag must snap back to the committed geometry.
                    self.scene_keys.clear()
                    self._request_revision_scenes(self.current_revision_id)
                return
            self.current_revision_id = result["revision_id"]
            self.viewing_revision_id = result["revision_id"]
            self.persisted_version = result["revision_id"]
            self.scene_load_state = "loading"
            self._last_change_summary = note
            self.read_only = False
            self.scene_keys.clear()
            self._last_checks = {"review_status": result.get("review_status"),
                                 "counts": result.get("checks_summary") or {}}
            self._request_revision_scenes(result["revision_id"])
            added = (result.get("diff") or {}).get("added") or []
            self.agent.add_system_note(
                f"{note} · revision {result['revision_id']}")
            self._emit_meta()
            if focus is not None:
                self.design_view.pcb2d.center_on_world(*focus)
                self.design_view.schematic.center_on_world(*focus)
            if added:
                self.design_view.select("component", added[0])

        self.projects.patch(operations, self.current_revision_id, done,
                            operation_id=operation_id)

    def _undo(self) -> None:
        if self._agent_busy():
            self.toast.emit("Agent 正在修改该工程，停止或等待任务结束后再撤销。")
            return

        def done(result, error):
            if error:
                self.toast.emit(error.get("message", "Undo failed"))
                return
            self.current_revision_id = result.get("revision_id")
            self.viewing_revision_id = self.current_revision_id
            self.persisted_version = self.current_revision_id
            self.scene_load_state = "loading"
            self.scene_keys.clear()
            self._request_revision_scenes(self.current_revision_id)
            self.agent.add_system_note(f"Undo created {result.get('revision_id')}")
            self._emit_meta()

        self.projects.undo(done)

    def _redo(self) -> None:
        if self._agent_busy():
            self.toast.emit("Agent 正在修改该工程，停止或等待任务结束后再重做。")
            return

        def done(result, error):
            if error:
                self.toast.emit(error.get("message", "Redo failed"))
                return
            self.current_revision_id = result.get("revision_id")
            self.viewing_revision_id = self.current_revision_id
            self.persisted_version = self.current_revision_id
            self.scene_load_state = "loading"
            self.scene_keys.clear()
            self._request_revision_scenes(self.current_revision_id)
            self.agent.add_system_note(f"Redo created {result.get('revision_id')}")
            self._emit_meta()

        self.projects.redo(done)

    def _on_selection(self, kind: str, object_id: str) -> None:
        self.selected_kind = kind
        self.selected_id = object_id
        self.selection_changed.emit(kind, object_id)

    def _agent_busy(self) -> bool:
        return bool(self.runs.run_id
                    and self.runs.project_id == self.active_project_id
                    and self.runs.state not in (
                        "SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "RECOVERABLE"))

    def delete_selected(self) -> None:
        if self._agent_busy():
            self.toast.emit("Agent 正在修改该工程，停止或等待任务结束后再编辑。")
            return
        if self.selected_kind != "component" or not self.selected_id:
            self.toast.emit("请先选中一个器件。")
            return
        if self.read_only:
            self.toast.emit("该版本为只读，请在当前版本上编辑。")
            return
        self._patch([{"op": "delete_component", "component_id": self.selected_id}],
                    "Deleted 1 component",
                    operation_id=f"delete_{uuid.uuid4().hex}")

    def rotate_selected(self) -> None:
        if self._agent_busy():
            self.toast.emit("Agent 正在修改该工程，停止或等待任务结束后再编辑。")
            return
        if self.selected_kind != "component" or not self.selected_id:
            self.toast.emit("请先选中一个器件。")
            return
        if self.read_only:
            self.toast.emit("该版本为只读，请在当前版本上编辑。")
            return
        self._patch([{"op": "rotate_component", "component_id": self.selected_id,
                      "rotation_deg": 90}], "Rotated 1 component",
                    operation_id=f"rotate_{uuid.uuid4().hex}")

    def save_now(self) -> str:
        """Edits commit immediately; this only reports the real state."""

        if not self.active_project_id:
            return "尚未打开工程"
        if self.pending_operation_id or (
                self.persisted_version and self.displayed_version != self.persisted_version):
            return "设计已保存 · 画布更新中"
        return "已是最新"

    def selection(self) -> tuple[str, str]:
        return self.selected_kind, self.selected_id

    selection_changed = Signal(str, str)

    def _ask_part(self, part_id: str, mpn: str) -> None:
        self.agent.prefill(
            f"Explain {mpn} ({part_id}): key limits, pin mapping and whether it fits "
            f"the current project.")
        self.agent.composer.setFocus()

    def _set_follow(self, follow: bool) -> None:
        self.runs.follow_build = follow

    def _on_error(self, error: dict[str, Any]) -> None:
        self._pending_blank = False
        self.agent.add_error_card(error.get("message", "Request failed"),
                                  error.get("code", ""), error.get("next_steps"))

    # ------------------------------------------------------------------ misc
    def show_empty(self) -> None:
        self.center.setCurrentWidget(self.empty)
        self.active_project_id = None
        self.current_revision_id = None
        self.viewing_revision_id = None
        self.read_only = False
        self.persisted_version = None
        self.displayed_version = None
        self.displayed_target = {"view": "schematic"}
        self.pending_operation_id = None
        self.scene_load_state = "idle"
        self._last_change_summary = ""
        self.selected_kind = ""
        self.selected_id = ""
        self._pending_place_part = None
        self.scene_keys.clear()
        self.agent.clear()
        self.design_view.clear()
        self.resources.set_edit_enabled(False)
        self._pending_template = None
        self._pending_prompt = None
        self._emit_meta()

    def open_project(self, project_id: str, revision_id: str | None = None) -> None:
        self._pending_revision = revision_id
        self.projects.open(project_id)

    def select_in_views(self, kind: str, object_id: str) -> None:
        self.design_view.select(kind, object_id)

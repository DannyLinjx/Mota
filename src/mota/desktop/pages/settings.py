"""AI connections: multiple provider profiles, capabilities and preferences."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from mota.desktop.bridge import EngineBridge
from mota.desktop.controllers import ConfigController, SessionController, TaskController
from mota.desktop.theme.tokens import COLORS, STATUS_COLORS

PROVIDERS = [("OpenAI", "openai"), ("DeepSeek", "deepseek"),
             ("Custom OpenAI-compatible", "custom_openai_compatible")]
FORMATS = [("Responses", "responses"), ("Chat Completions", "chat_completions")]
THINKING_MODES = [("Disabled", "disabled"), ("Auto (provider default)", "auto"),
                  ("Enabled", "enabled")]
EFFORTS = [("—", None), ("Low", "low"), ("High", "high"), ("Max", "max")]
SUITES = [("Text", "text"), ("Tool roundtrip", "tool_roundtrip"), ("Vision", "vision")]

PRESETS = {
    "openai": {"api_format": "responses", "base_url": "https://api.openai.com/v1",
               "model": "gpt-5.4", "thinking_mode": "disabled", "thinking_effort": None,
               "strict_tool_schema": True, "supports_vision": True},
    "deepseek": {"api_format": "chat_completions", "base_url": "https://api.deepseek.com",
                 "model": "deepseek-flash", "thinking_mode": "disabled",
                 "thinking_effort": None, "strict_tool_schema": False,
                 "supports_vision": True},
    "custom_openai_compatible": {"api_format": "chat_completions", "base_url": "",
                                 "model": "", "thinking_mode": "disabled",
                                 "thinking_effort": None, "strict_tool_schema": False,
                                 "supports_vision": False},
}


class SettingsPage(QWidget):
    config_changed = Signal(dict)

    def __init__(self, bridge: EngineBridge, session: SessionController,
                 config: ConfigController, tasks: TaskController,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.session = session
        self.config_controller = config
        self.tasks = tasks
        self.config: dict[str, Any] = {}
        self.profiles: list[dict[str, Any]] = []
        self.profile: dict[str, Any] | None = None
        self._dirty = False
        self._build()
        self.config_controller.config_loaded.connect(self._apply_config)
        self.config_controller.error.connect(self._show_error)
        self.tasks.event.connect(self._task_event)
        self.tasks.finished.connect(self._task_finished)

    # ------------------------------------------------------------------- build
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(14)
        header = QHBoxLayout()
        title = QLabel("AI connections")
        title.setObjectName("PageTitle")
        self.header_status = QLabel("")
        self.header_status.setObjectName("Muted")
        header.addWidget(title)
        header.addWidget(self.header_status, 1)
        outer.addLayout(header)
        note = QLabel("Saved connections are separate from your account. The key is written "
                      "through the controlled secret channel and never shown again. "
                      "Changing the default only affects new runs.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        outer.addWidget(note)

        body = QHBoxLayout()
        body.setSpacing(14)
        left = QFrame()
        left.setObjectName("Panel")
        left.setFixedWidth(248)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)
        self.profile_list = QListWidget()
        self.profile_list.currentRowChanged.connect(self._profile_selected)
        left_layout.addWidget(self.profile_list, 1)
        buttons = QHBoxLayout()
        add_button = QPushButton("New")
        add_button.setObjectName("Tool")
        add_button.clicked.connect(self._new_profile)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("Tool")
        self.delete_button.clicked.connect(self._delete_profile)
        buttons.addWidget(add_button)
        buttons.addWidget(self.delete_button)
        left_layout.addLayout(buttons)
        body.addWidget(left)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        scroll.setWidget(page)
        form_layout = QVBoxLayout(page)
        form_layout.setContentsMargins(0, 0, 8, 0)
        form_layout.setSpacing(14)

        connection = self._group("Connection")
        conn_form = self._form(connection)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Connection name")
        self.provider = QComboBox()
        for label, value in PROVIDERS:
            self.provider.addItem(label, value)
        self.api_format = QComboBox()
        for label, value in FORMATS:
            self.api_format.addItem(label, value)
        self.base_url = QLineEdit()
        self.model = QLineEdit()
        self.model.setPlaceholderText("Model id, for example deepseek-flash")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Not configured — type to replace")
        key_row = QHBoxLayout()
        show = QPushButton("Show")
        show.setObjectName("Tool")
        show.pressed.connect(lambda: self.key.setEchoMode(QLineEdit.EchoMode.Normal))
        show.released.connect(lambda: self.key.setEchoMode(QLineEdit.EchoMode.Password))
        clear = QPushButton("Clear")
        clear.setObjectName("Tool")
        clear.clicked.connect(self._clear_key)
        key_row.addWidget(self.key, 1)
        key_row.addWidget(show)
        key_row.addWidget(clear)
        key_widget = QWidget()
        key_widget.setLayout(key_row)
        self.session_only = QCheckBox("Use for this session only")
        self.key_status = QLabel("")
        self.key_status.setObjectName("Muted")
        self.key_status.setWordWrap(True)
        self._labeled(conn_form, "Connection name", self.name)
        self._labeled(conn_form, "Provider", self.provider)
        self._labeled(conn_form, "API format", self.api_format)
        self._labeled(conn_form, "Base URL", self.base_url)
        self._labeled(conn_form, "Model", self.model)
        self._labeled(conn_form, "API key", key_widget)
        conn_form.addRow("", self.session_only)
        conn_form.addRow("", self.key_status)
        form_layout.addWidget(connection)

        behavior = self._group("Model behavior")
        behavior_form = self._form(behavior)
        self.stream = QCheckBox("Stream responses")
        self.thinking_mode = QComboBox()
        for label, value in THINKING_MODES:
            self.thinking_mode.addItem(label, value)
        self.thinking_effort = QComboBox()
        for label, value in EFFORTS:
            self.thinking_effort.addItem(label, value)
        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(256, 131072)
        self.max_tokens.setSingleStep(512)
        self.max_tokens.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.max_tokens.setMinimumHeight(38)
        self.supports_vision = QCheckBox("This model accepts images")
        self.supports_vision.setToolTip(
            "DeepSeek Flash accepts images; DeepSeek Pro and reasoner models do not. "
            "The vision suite reports UNSUPPORTED without sending a request.")
        behavior_form.addRow("", self.stream)
        self._labeled(behavior_form, "Thinking", self.thinking_mode)
        self._labeled(behavior_form, "Thinking effort", self.thinking_effort)
        self._labeled(behavior_form, "Max output tokens", self.max_tokens)
        behavior_form.addRow("", self.supports_vision)
        self.thinking_mode.currentIndexChanged.connect(self._thinking_changed)
        form_layout.addWidget(behavior)

        advanced = self._group("Advanced")
        advanced_form = self._form(advanced)
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(5.0, 600.0)
        self.timeout.setSingleStep(5.0)
        self.timeout.setSuffix(" s")
        self.timeout.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.timeout.setMinimumHeight(38)
        self.strict = QCheckBox("Request strict tool schemas when the endpoint supports it")
        strict_note = QLabel("Strict is confirmed by the tool_roundtrip suite, not by this "
                             "switch. The probe result is the evidence.")
        strict_note.setObjectName("Muted")
        strict_note.setWordWrap(True)
        self._labeled(advanced_form, "Request timeout", self.timeout)
        advanced_form.addRow("", self.strict)
        advanced_form.addRow("", strict_note)
        form_layout.addWidget(advanced)

        capabilities = self._group("Capabilities")
        cap_layout = capabilities.content_layout  # type: ignore[attr-defined]
        suite_row = QHBoxLayout()
        self.suite_boxes: dict[str, QCheckBox] = {}
        for label, value in SUITES:
            box = QCheckBox(label)
            box.setChecked(value in ("text", "tool_roundtrip"))
            self.suite_boxes[value] = box
            suite_row.addWidget(box)
        suite_row.addStretch(1)
        self.probe_button = QPushButton("Test connection")
        self.probe_button.clicked.connect(self._probe)
        suite_row.addWidget(self.probe_button)
        cap_layout.addLayout(suite_row)
        self.probe_summary = QLabel("")
        self.probe_summary.setWordWrap(True)
        self.probe_summary.setObjectName("Muted")
        cap_layout.addWidget(self.probe_summary)
        self.probe_results = QListWidget()
        self.probe_results.setMinimumHeight(112)
        cap_layout.addWidget(self.probe_results)
        cap_note = QLabel("Probes send synthetic text and a generated image only. Project "
                          "data, requirement documents and memory are never sent. Request "
                          "caps: text 1, tool roundtrip 2, vision 1.")
        cap_note.setObjectName("Muted")
        cap_note.setWordWrap(True)
        cap_layout.addWidget(cap_note)
        form_layout.addWidget(capabilities)

        preferences = self._group("Workspace preferences")
        pref_form = self._form(preferences)
        self.units = QComboBox()
        self.units.addItems(["mm", "in"])
        self.default_view = QComboBox()
        self.default_view.addItems(["schematic", "pcb2d", "pcb3d"])
        self.grid = QCheckBox("Show grid")
        self.snap = QDoubleSpinBox()
        self.snap.setDecimals(3)
        self.snap.setRange(0.01, 5.0)
        self.snap.setSingleStep(0.05)
        self.snap.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.snap.setMinimumHeight(38)
        self.reduced = QCheckBox("Reduced motion")
        self.follow = QCheckBox("Follow build")
        self.ctrl_enter = QCheckBox("Send with Ctrl/Cmd+Enter")
        self.restore = QCheckBox("Restore last project on start")
        self.remember = QCheckBox("Remember me on this machine")
        self._labeled(pref_form, "Display units", self.units)
        self._labeled(pref_form, "Default view", self.default_view)
        pref_form.addRow("", self.grid)
        self._labeled(pref_form, "Snap (mm)", self.snap)
        pref_form.addRow("", self.reduced)
        pref_form.addRow("", self.follow)
        pref_form.addRow("", self.ctrl_enter)
        pref_form.addRow("", self.restore)
        pref_form.addRow("", self.remember)
        self.pref_save = QPushButton("Save preferences")
        self.pref_save.setObjectName("Tool")
        self.pref_save.clicked.connect(self._save_preferences)
        pref_form.addRow("", self.pref_save)
        form_layout.addWidget(preferences)
        form_layout.addStretch(1)
        body.addWidget(scroll, 1)
        outer.addLayout(body, 1)

        footer = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        self.default_button = QPushButton("Set as default for new runs")
        self.default_button.clicked.connect(self._set_default)
        self.save_button = QPushButton("Save connection")
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(self._save_connection)
        footer.addWidget(self.status, 1)
        footer.addWidget(self.default_button)
        footer.addWidget(self.save_button)
        outer.addLayout(footer)

        self.provider.currentIndexChanged.connect(self._provider_changed)
        for widget in (self.name, self.base_url, self.model):
            widget.textChanged.connect(self._mark_dirty)
        for combo in (self.api_format, self.thinking_mode, self.thinking_effort):
            combo.currentIndexChanged.connect(self._mark_dirty)
        for box in (self.stream, self.supports_vision, self.strict):
            box.toggled.connect(self._mark_dirty)
        for spin in (self.max_tokens, self.timeout):
            spin.valueChanged.connect(self._mark_dirty)
        self._thinking_changed()

    def _group(self, title: str) -> QFrame:
        box = QFrame()
        box.setObjectName("Raised")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        heading.setContentsMargins(14, 12, 14, 0)
        outer.addWidget(heading)
        content = QVBoxLayout()
        content.setContentsMargins(14, 6, 14, 14)
        content.setSpacing(8)
        outer.addLayout(content)
        box.content_layout = content  # type: ignore[attr-defined]
        return box

    def _form(self, box: QFrame) -> QFormLayout:
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        box.content_layout.addLayout(form)  # type: ignore[attr-defined]
        return form

    @staticmethod
    def _labeled(form: QFormLayout, label: str, widget: QWidget) -> None:
        """Label above the field, so the reading order survives narrow widths."""
        tag = QLabel(label)
        tag.setObjectName("Muted")
        form.addRow(tag)
        form.addRow(widget)

    # ------------------------------------------------------------------ config
    def load(self) -> None:
        self.config_controller.load()

    def _apply_config(self, config: dict[str, Any]) -> None:
        previous = self.profile["profile_id"] if self.profile else None
        self.config = config
        self.profiles = list(config.get("profiles") or [])
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        for profile in self.profiles:
            item = QListWidgetItem(
                f"{profile.get('name') or profile['profile_id']}\n"
                f"{profile.get('provider', '')} · {profile.get('api_format', '')}")
            item.setData(Qt.ItemDataRole.UserRole, profile["profile_id"])
            item.setSizeHint(QSize(0, 56))
            self.profile_list.addItem(item)
        self.profile_list.blockSignals(False)
        default_id = config.get("default_profile_id")
        target = previous if any(p["profile_id"] == previous for p in self.profiles) \
            else default_id
        index = next((i for i, p in enumerate(self.profiles)
                      if p["profile_id"] == target), 0)
        if self.profiles:
            self.profile_list.setCurrentRow(index)
            self._load_profile(self.profiles[index])
        self._apply_preferences(config)
        self.header_status.setText(
            f"config v{config.get('config_version', 2)} · default "
            f"{self._default_name(default_id)}")

    def _default_name(self, profile_id: str | None) -> str:
        for profile in self.profiles:
            if profile["profile_id"] == profile_id:
                return profile.get("name") or profile_id
        return "—"

    def _apply_preferences(self, config: dict[str, Any]) -> None:
        self.units.setCurrentText(config.get("units", "mm"))
        self.default_view.setCurrentText(config.get("default_view", "schematic"))
        self.grid.setChecked(bool(config.get("show_grid", True)))
        self.snap.setValue(float(config.get("snap_mm", 0.25)))
        self.reduced.setChecked(bool(config.get("reduced_motion", False)))
        self.follow.setChecked(bool(config.get("follow_build", True)))
        self.ctrl_enter.setChecked(bool(config.get("send_with_ctrl_enter", False)))
        self.restore.setChecked(bool(config.get("restore_last_project", True)))
        self.remember.setChecked(bool(config.get("remember_me", False)))

    def _profile_selected(self, row: int) -> None:
        if 0 <= row < len(self.profiles):
            self._load_profile(self.profiles[row])

    def _load_profile(self, profile: dict[str, Any]) -> None:
        self.profile = profile
        self.name.setText(profile.get("name", ""))
        index = self.provider.findData(profile.get("provider", "openai"))
        self.provider.blockSignals(True)
        self.provider.setCurrentIndex(max(0, index))
        self.provider.blockSignals(False)
        index = self.api_format.findData(profile.get("api_format", "responses"))
        self.api_format.setCurrentIndex(max(0, index))
        self.base_url.setText(profile.get("base_url", ""))
        self.model.setText(profile.get("model", ""))
        self.stream.setChecked(bool(profile.get("stream", True)))
        index = self.thinking_mode.findData(profile.get("thinking_mode", "disabled"))
        self.thinking_mode.setCurrentIndex(max(0, index))
        effort = profile.get("thinking_effort")
        index = self.thinking_effort.findData(effort) if effort else 0
        self.thinking_effort.setCurrentIndex(max(0, index))
        self.max_tokens.setValue(int(profile.get("max_output_tokens", 8192)))
        self.timeout.setValue(float(profile.get("request_timeout_seconds", 120.0)))
        self.strict.setChecked(bool(profile.get("strict_tool_schema", True)))
        self.supports_vision.setChecked(bool(profile.get("supports_vision", True)))
        self.key.clear()
        self.key.setPlaceholderText(
            "•••••••• configured — type to replace"
            if profile.get("api_key_configured")
            else "Not configured — type to replace")
        storage = profile.get("secret_storage", "none")
        capabilities = self.config.get("secret_storage_capabilities", {})
        self.session_only.setVisible(not capabilities.get("keychain", True))
        self.key_status.setText(
            f"Key {'configured' if profile.get('api_key_configured') else 'not configured'}"
            f" · storage {storage}"
            + ("" if capabilities.get("keychain", True)
               else " · OS keychain unavailable; session-only storage is used"))
        self.delete_button.setEnabled(len(self.profiles) > 1)
        self._thinking_changed()
        self._render_saved_results(profile)
        self._dirty = False
        self.status.setText("")
        self.save_button.setText(
            "Save connection" + (" · default" if self._is_default() else ""))

    def _is_default(self) -> bool:
        return bool(self.profile and self.profile["profile_id"] ==
                    self.config.get("default_profile_id"))

    def _mark_dirty(self, *_args) -> None:
        if not self.profile:
            return
        self._dirty = True
        self.status.setText("Unsaved changes")
        self.status.setStyleSheet(f"color: {COLORS['warning']};")

    def _provider_changed(self) -> None:
        if not self.profile:
            return
        provider = self.provider.currentData() or "openai"
        preset = PRESETS.get(provider, PRESETS["openai"])
        self.api_format.setCurrentIndex(max(0, self.api_format.findData(
            preset["api_format"])))
        self.base_url.setText(preset["base_url"])
        self.model.setText(preset["model"])
        self.strict.setChecked(bool(preset["strict_tool_schema"]))
        self.supports_vision.setChecked(bool(preset["supports_vision"]))
        index = self.thinking_mode.findData(preset["thinking_mode"])
        self.thinking_mode.setCurrentIndex(max(0, index))
        self._mark_dirty()

    def _thinking_changed(self) -> None:
        enabled = self.thinking_mode.currentData() == "enabled"
        self.thinking_effort.setEnabled(enabled)
        if not enabled:
            self.thinking_effort.setCurrentIndex(0)
        elif self.thinking_effort.currentData() is None:
            self.thinking_effort.setCurrentIndex(
                max(0, self.thinking_effort.findData("high")))

    # ------------------------------------------------------------------- save
    def _profile_payload(self) -> dict[str, Any]:
        if not self.profile:
            return {}
        effort = (self.thinking_effort.currentData()
                  if self.thinking_mode.currentData() == "enabled" else None)
        return {
            "profile_id": self.profile["profile_id"],
            "name": self.name.text().strip() or self.profile["profile_id"],
            "provider": self.provider.currentData() or "openai",
            "api_format": self.api_format.currentData() or "responses",
            "base_url": self.base_url.text().strip(),
            "model": self.model.text().strip(),
            "stream": self.stream.isChecked(),
            "thinking_mode": self.thinking_mode.currentData() or "disabled",
            "thinking_effort": effort,
            "max_output_tokens": int(self.max_tokens.value()),
            "request_timeout_seconds": float(self.timeout.value()),
            "strict_tool_schema": self.strict.isChecked(),
            "supports_vision": self.supports_vision.isChecked(),
        }

    def _save_changes(self, extra: dict[str, Any] | None = None,
                      callback=None) -> None:
        if not self.profile:
            return
        payload = self._profile_payload()
        changes: dict[str, Any] = {"profiles": [payload]}
        if self.key.text():
            changes["api_key_action"] = "set"
            changes["api_key"] = self.key.text()
            changes["api_key_profile_id"] = payload["profile_id"]
            changes["api_key_session_only"] = self.session_only.isChecked()
        if extra:
            changes.update(extra)

        def done(result, error):
            if error:
                self._show_error(error)
                if callback:
                    callback(False)
                return
            self.key.clear()
            self._dirty = False
            self.config_controller.load()
            self.config_changed.emit(result["config"])
            if callback:
                callback(True)

        self.config_controller.update(changes, done)

    def _save_connection(self) -> None:
        def done(ok: bool) -> None:
            if ok:
                self.status.setText("Connection saved")
                self.status.setStyleSheet(f"color: {COLORS['success']};")

        self._save_changes(callback=done)

    def _save_preferences(self) -> None:
        changes = {
            "units": self.units.currentText(),
            "default_view": self.default_view.currentText(),
            "show_grid": self.grid.isChecked(),
            "snap_mm": float(self.snap.value()),
            "reduced_motion": self.reduced.isChecked(),
            "follow_build": self.follow.isChecked(),
            "send_with_ctrl_enter": self.ctrl_enter.isChecked(),
            "restore_last_project": self.restore.isChecked(),
            "remember_me": self.remember.isChecked(),
        }

        def done(result, error):
            if error:
                self._show_error(error)
                return
            self.status.setText("Preferences saved")
            self.status.setStyleSheet(f"color: {COLORS['success']};")
            self.config_controller.load()
            self.config_changed.emit(result["config"])

        self.config_controller.update(changes, done)

    def _set_default(self) -> None:
        if not self.profile:
            return

        def done(result, error):
            if error:
                self._show_error(error)
                return
            self.status.setText("Default for new runs updated; the current run keeps its "
                                "frozen connection.")
            self.status.setStyleSheet(f"color: {COLORS['success']};")
            self.config_controller.load()
            self.config_changed.emit(result["config"])

        self.config_controller.update(
            {"set_default_profile_id": self.profile["profile_id"]}, done)

    def _clear_key(self) -> None:
        if not self.profile:
            return

        def done(result, error):
            if error:
                self._show_error(error)
                return
            self.status.setText("API key removed")
            self.status.setStyleSheet(f"color: {COLORS['success']};")
            self.config_controller.load()
            self.config_changed.emit(result["config"])

        self.config_controller.update({
            "api_key_action": "clear",
            "api_key_profile_id": self.profile["profile_id"]}, done)

    # --------------------------------------------------------------- profiles
    def _new_profile(self) -> None:
        labels = [label for label, _ in PROVIDERS]
        label, ok = QInputDialog.getItem(self, "New connection", "Provider", labels, 0,
                                         False)
        if not ok:
            return
        provider = dict((label_, value) for label_, value in PROVIDERS)[label]
        preset = PRESETS.get(provider, PRESETS["openai"])
        existing = {p["profile_id"] for p in self.profiles}
        index = 1
        while f"{provider}-{index}" in existing:
            index += 1
        profile_id = f"{provider}-{index}"
        changes = {
            "profiles": [{
                "profile_id": profile_id,
                "name": label,
                **preset,
                "stream": True,
                "max_output_tokens": 8192,
                "request_timeout_seconds": 120.0,
            }],
        }

        def done(result, error):
            if error:
                self._show_error(error)
                return
            self.config_controller.load()
            self.config_changed.emit(result["config"])

        self.config_controller.update(changes, done)

    def _delete_profile(self) -> None:
        if not self.profile or len(self.profiles) <= 1:
            return
        name = self.profile.get("name") or self.profile["profile_id"]
        answer = QMessageBox.question(
            self, "Delete connection",
            f"Delete “{name}”? The stored key for this connection is removed. "
            f"Existing runs keep their frozen connection.")
        if answer != QMessageBox.StandardButton.Yes:
            return

        def done(result, error):
            if error:
                self._show_error(error)
                return
            self.profile = None
            self.config_controller.load()
            self.config_changed.emit(result["config"])

        self.config_controller.update(
            {"delete_profile_id": self.profile["profile_id"]}, done)

    # ------------------------------------------------------------------ probe
    def _probe(self) -> None:
        if not self.profile:
            return
        suites = [value for value, box in self.suite_boxes.items() if box.isChecked()]
        if not suites:
            self.probe_summary.setText("Choose at least one capability suite.")
            return
        self.probe_results.clear()
        self.probe_summary.setText("Saving the connection, then testing with synthetic "
                                   "content…")
        self.probe_button.setEnabled(False)

        def saved(ok: bool) -> None:
            if not ok:
                self.probe_button.setEnabled(True)
                self.probe_summary.setText("Could not save the connection before testing.")
                return

            def started(result, error):
                if error:
                    self.probe_button.setEnabled(True)
                    self._show_error(error)
                    return
                self.tasks.inspect(result["task_id"])
                self.probe_summary.setText("Waiting for the model response…")

            self.config_controller.probe(started, profile_id=self.profile["profile_id"],
                                         suites=suites)

        if self._dirty or self.key.text():
            self._save_changes(callback=saved)
        else:
            saved(True)

    def _task_event(self, event: dict[str, Any]) -> None:
        if event.get("task_kind") != "model_probe":
            return
        payload = event.get("payload") or {}
        if event.get("type") == "task.progress":
            message = payload.get("message")
            if not message and payload.get("stage") == "probe_request":
                message = (f"{payload.get('suite')} request "
                           f"{payload.get('request')} → {payload.get('model')}")
            if message:
                self.probe_summary.setText(message)

    def _task_finished(self, payload: dict[str, Any]) -> None:
        self.probe_button.setEnabled(True)
        status = payload.get("status")
        if status == "FAILED":
            error = payload.get("error") or {}
            self.probe_summary.setText(f"Probe failed: {error.get('message', '')}")
            self.probe_summary.setStyleSheet(f"color: {COLORS['error']};")
            return
        result = payload.get("result") or {}
        self._render_results(result)
        self.config_controller.load()

    def _render_results(self, result: dict[str, Any]) -> None:
        self.probe_results.clear()
        suites = result.get("suites") or []
        passed = sum(1 for s in suites if s.get("status") == "PASS")
        usage = result.get("usage_total") or {}
        self.probe_summary.setText(
            f"{passed}/{len(suites)} suite(s) passed · tokens in "
            f"{usage.get('input_tokens', 0)} / out {usage.get('output_tokens', 0)} · "
            f"image input {'supported' if result.get('vision_supported') else 'unsupported'}"
            f" · {result.get('billing_note', '')}")
        self.probe_summary.setStyleSheet("")
        for suite in suites:
            status = suite.get("status", "FAILED")
            item = QListWidgetItem(
                f"[{status}] {suite.get('suite')} · {suite.get('requests', 0)} request(s)"
                f" · {suite.get('latency_ms', 0)} ms\n{suite.get('detail', '')}")
            item.setForeground(Qt.GlobalColor.white)
            color = STATUS_COLORS.get(status, COLORS["textSecondary"])
            item.setToolTip(f"Status colour: {color}")
            self.probe_results.addItem(item)

    def _render_saved_results(self, profile: dict[str, Any]) -> None:
        self.probe_results.clear()
        results = profile.get("test_results") or {}
        if not results:
            self.probe_summary.setText("No capability test recorded for this connection.")
            self.probe_summary.setStyleSheet("")
            return
        rows = []
        for suite, entry in results.items():
            status = entry.get("status", "FAILED")
            rows.append(
                f"[{status}] {suite} · {entry.get('requests', 0)} request(s) · "
                f"config v{entry.get('config_version', '?')} · "
                f"{entry.get('tested_at', '')[:16].replace('T', ' ')}\n"
                f"{entry.get('detail', '')}")
        self.probe_summary.setText(f"Last recorded results ({len(rows)} suite(s))")
        self.probe_summary.setStyleSheet("")
        for row in rows:
            item = QListWidgetItem(row)
            item.setForeground(Qt.GlobalColor.white)
            self.probe_results.addItem(item)

    def _show_error(self, error: dict[str, Any]) -> None:
        self.status.setText(f"{error.get('code', '')}: {error.get('message', '')}")
        self.status.setStyleSheet(f"color: {COLORS['error']};")
        QTimer.singleShot(8000, lambda: self.status.setStyleSheet(""))

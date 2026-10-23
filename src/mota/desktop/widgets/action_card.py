"""Agent execution cards and result card (event-driven, no scripted success)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from mota.desktop.theme.tokens import COLORS

TOOL_TITLES = {
    "list_templates": "List templates",
    "inspect_part": "Resolve part",
    "search_catalog": "Search the catalog",
    "read_requirement": "Read requirement",
    "read_rules": "Read reviewed rules",
    "ask_user": "Ask for requirements",
    "prepare_design": "Build the schematic",
    "apply_design_patch": "Apply design changes",
    "validate_design": "Run Mota checks",
    "build_board": "Place and route",
    "render_preview": "Prepare views",
    "summarize_build": "Summarize the build",
}


def _format_result(tool: str, result: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if tool == "inspect_part":
        lines.append(f"{result.get('mpn', '')} · {result.get('package', '')}")
        lines.append(f"{result.get('pin_count', '?')} pins · "
                     f"{'mapped' if result.get('pin_pad_map') else 'mapping missing'}")
        diagnostics = result.get("diagnostics") or []
        if diagnostics:
            lines.append(f"{len(diagnostics)} capability note(s)")
    elif tool == "prepare_design":
        lines.append(f"{result.get('components', 0)} components · "
                     f"{result.get('nets', 0)} nets")
        lines.append(f"Draft seq {result.get('draft_seq', '?')}")
    elif tool == "validate_design":
        counts = result.get("counts") or {}
        lines.append(f"{result.get('review_status', 'DRAFT')} · "
                     f"{counts.get('PASS', 0)} passed · {counts.get('FAIL', 0)} failed · "
                     f"{counts.get('NOT_CHECKED', 0)} not checked")
    elif tool == "build_board":
        lines.append(f"{result.get('tracks', 0)} track segments · "
                     f"{result.get('vias', 0)} vias")
        unrouted = result.get("unrouted_nets") or []
        lines.append("All nets routed" if not unrouted
                     else f"{len(unrouted)} net(s) unresolved: {', '.join(unrouted[:4])}")
    elif tool == "render_preview":
        if result.get("views"):
            lines.append(f"{len(result['views'])} views: {', '.join(result['views'])}")
        else:
            lines.append(f"view {result.get('view', '')} · "
                         f"{result.get('object_count', 0)} objects")
        if result.get("missing_models"):
            lines.append(f"{len(result['missing_models'])} approximate model(s)")
    elif tool == "list_templates":
        lines.append(f"{len(result.get('templates', []))} supported template(s)")
    elif tool == "read_requirement":
        lines.append(f"{result.get('filename', '')} · {result.get('chars', 0)} chars")
        if result.get("truncated"):
            lines.append("Excerpt truncated for the model")
    for key in ("summary",):
        if result.get(key):
            lines.append(str(result[key]))
    return lines


class ActionCard(QFrame):
    locate = Signal(str)
    open_checks = Signal()

    def __init__(self, call_id: str, tool: str, arguments: dict[str, Any],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Raised")
        self.call_id = call_id
        self.tool = tool
        self.arguments = arguments
        self.object_ids: list[str] = []
        self._expanded = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(4)
        header = QHBoxLayout()
        header.setSpacing(6)
        self.chevron = QPushButton("▾")
        self.chevron.setObjectName("Tool")
        self.chevron.setFixedWidth(22)
        self.chevron.setToolTip("Collapse or expand this step")
        self.chevron.clicked.connect(self.toggle)
        self.title = QLabel(TOOL_TITLES.get(tool, tool.replace("_", " ").title()))
        self.title.setObjectName("ToolTitle")
        self.title.setWordWrap(True)
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Preferred)
        self.status = QLabel("Running")
        self.status.setStyleSheet(f"color: {COLORS['info']}; font-weight: 600;")
        header.addWidget(self.chevron)
        header.addWidget(self.title, 1)
        header.addWidget(self.status, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(header)
        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(26, 0, 0, 0)
        body_layout.setSpacing(4)
        self.detail = QLabel(self._argument_line())
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        body_layout.addWidget(self.detail)
        self.locate_row = QHBoxLayout()
        self.locate_row.setSpacing(4)
        body_layout.addLayout(self.locate_row)
        self.buttons = QHBoxLayout()
        self.checks_button = QPushButton("Open checks")
        self.checks_button.setObjectName("Tool")
        self.checks_button.hide()
        self.checks_button.clicked.connect(self.open_checks.emit)
        self.buttons.addWidget(self.checks_button)
        self.buttons.addStretch(1)
        body_layout.addLayout(self.buttons)
        layout.addWidget(self.body)

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self.body.setVisible(self._expanded)
        self.chevron.setText("▾" if self._expanded else "▸")

    def expand(self) -> None:
        if not self._expanded:
            self.toggle()

    def collapse(self) -> None:
        if self._expanded:
            self.toggle()

    def _argument_line(self) -> str:
        args = self.arguments
        if self.tool == "inspect_part":
            return f"Part {args.get('asset_id', '')}"
        if self.tool == "list_templates":
            return f"Scenario {args.get('scenario', '') or 'any'}"
        if self.tool == "prepare_design":
            return f"Template {args.get('template_id', '')}"
        if self.tool == "build_board":
            return "Module-rule placement · two-layer grid routing"
        if self.tool == "validate_design":
            return "Mota checks on the current design version"
        if self.tool == "render_preview":
            return f"View {args.get('view', '')}"
        return ""

    def complete(self, result: dict[str, Any], duration_ms: int = 0) -> None:
        self.status.setText("Completed")
        self.status.setStyleSheet(f"color: {COLORS['success']}; font-weight: 600;")
        lines = _format_result(self.tool, result)
        if duration_ms:
            lines.append(f"{duration_ms} ms")
        self.object_ids = list(result.get("affected_object_ids") or [])
        while self.locate_row.count():
            item = self.locate_row.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for object_id in self.object_ids[:4]:
            label = str((result.get("affected_refs") or {}).get(object_id, object_id))
            button = QPushButton(label)
            button.setObjectName("Tool")
            button.setToolTip(f"Select {label} in the current view")
            button.clicked.connect(
                lambda _checked=False, oid=object_id: self.locate.emit(oid))
            self.locate_row.addWidget(button)
        self.locate_row.addStretch(1)
        if self.object_ids:
            refs = result.get("affected_refs") or {}
            labels = [str(refs.get(oid, oid)) for oid in self.object_ids[:4]]
            lines.append("Affected: " + ", ".join(labels)
                         + (f" +{len(self.object_ids) - 4}" if len(self.object_ids) > 4
                            else ""))
        self.detail.setText("\n".join(lines) or "Completed")
        if self.tool == "validate_design":
            self.checks_button.show()
        self.collapse()

    def fail(self, error: dict[str, Any], retryable: bool = False) -> None:
        self.status.setText("Failed")
        self.status.setStyleSheet(f"color: {COLORS['error']}; font-weight: 600;")
        message = error.get("message", "Tool failed")
        self.detail.setText(message + (" · retryable" if retryable else ""))
        self.expand()


class ResultCard(QFrame):
    open_checks = Signal()
    locate_issue = Signal()

    def __init__(self, commit: dict[str, Any], checks: dict[str, Any],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Raised")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        revision = commit.get("revision_id", "")
        saved = QLabel(f"Saved as revision {revision.split('_')[-1].lstrip('0') or revision}")
        saved.setStyleSheet(f"color: {COLORS['success']}; font-weight: 600;")
        layout.addWidget(saved)
        review = checks.get("review_status", "DRAFT")
        review_text = ("✓ Ready for review" if review == "READY_FOR_REVIEW"
                       else "! Review blocked" if review == "REVIEW_BLOCKED"
                       else review.replace("_", " ").title())
        review_label = QLabel(review_text)
        color = COLORS["success"] if review == "READY_FOR_REVIEW" else COLORS["warning"]
        review_label.setStyleSheet(f"color: {color};")
        layout.addWidget(review_label)
        counts = checks.get("counts") or {}
        checks_label = QLabel(
            f"Mota checks: {counts.get('PASS', 0)} passed · "
            f"{counts.get('FAIL', 0)} failed · {counts.get('WARN', 0)} warnings · "
            f"{counts.get('NOT_CHECKED', 0)} not checked")
        checks_label.setObjectName("Muted")
        checks_label.setWordWrap(True)
        layout.addWidget(checks_label)
        row = QHBoxLayout()
        button = QPushButton("Open checks")
        button.setObjectName("Tool")
        button.clicked.connect(self.open_checks.emit)
        row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)


class ErrorCard(QFrame):
    retry = Signal()

    def __init__(self, message: str, code: str = "", next_steps: list[str] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Raised")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        title = QLabel("✕ Build stopped" if code else "! Problem")
        title.setStyleSheet(f"color: {COLORS['error']}; font-weight: 600;")
        layout.addWidget(title)
        body = QLabel(message)
        body.setWordWrap(True)
        layout.addWidget(body)
        if code:
            code_label = QLabel(f"Code {code}")
            code_label.setObjectName("Mono")
            layout.addWidget(code_label)
        for step in next_steps or []:
            step_label = QLabel(f"→ {step}")
            step_label.setObjectName("Muted")
            step_label.setWordWrap(True)
            layout.addWidget(step_label)

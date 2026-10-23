"""Right agent panel: conversation, real tool cards, progress and composer."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QElapsedTimer, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from mota.desktop.theme.tokens import COLORS
from mota.desktop.widgets.action_card import ActionCard, ErrorCard, ResultCard
from mota.desktop.widgets.clarification_card import ClarificationCard


class Composer(QPlainTextEdit):
    submit = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._composing = False
        self.setPlaceholderText("Describe a board, ask a question, or attach requirements…")
        self.setFixedHeight(96)
        self.setTabChangesFocus(True)

    def inputMethodEvent(self, event) -> None:  # noqa: N802
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            if self._composing:
                super().keyPressEvent(event)
                return
            self.submit.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class AgentPanel(QFrame):
    send_requested = Signal(str)
    attach_requested = Signal()
    cancel_requested = Signal()
    open_settings = Signal()
    answer_submitted = Signal(str, dict)
    follow_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setMinimumWidth(300)
        self.setMaximumWidth(480)
        self._waiting_for_user = False
        self._tokens = 0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QFrame()
        header.setFixedHeight(48)
        self.header = header
        header.setStyleSheet(f"background: {COLORS['shell']};"
                             f"border-bottom: 1px solid {COLORS['border']};")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 6, 8, 6)
        title = QLabel("Mota Agent")
        title.setObjectName("SectionTitle")
        self.model_label = QLabel("Template build · no model calls")
        self.model_label.setObjectName("Muted")
        settings = QPushButton("⚙")
        settings.setObjectName("Tool")
        settings.setToolTip("Open model settings")
        settings.clicked.connect(self.open_settings.emit)
        header_layout.addWidget(title)
        header_layout.addWidget(self.model_label, 1)
        header_layout.addWidget(settings)
        layout.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.conversation = QWidget()
        self.conversation_layout = QVBoxLayout(self.conversation)
        self.conversation_layout.setContentsMargins(10, 10, 10, 10)
        self.conversation_layout.setSpacing(8)
        self.conversation_layout.addStretch(1)
        self.scroll.setWidget(self.conversation)
        self.scroll.verticalScrollBar().valueChanged.connect(self._scroll_changed)
        layout.addWidget(self.scroll, 1)

        self.new_activity = QPushButton("New activity ↓")
        self.new_activity.setObjectName("Tool")
        self.new_activity.setVisible(False)
        self.new_activity.clicked.connect(self.scroll_to_latest)
        new_row = QHBoxLayout()
        new_row.setContentsMargins(12, 0, 12, 0)
        new_row.addStretch(1)
        new_row.addWidget(self.new_activity)
        layout.addLayout(new_row)

        self.progress = QFrame()
        self.progress.setStyleSheet(
            f"background: {COLORS['shell']}; border-top: 1px solid {COLORS['border']};")
        progress_layout = QVBoxLayout(self.progress)
        progress_layout.setContentsMargins(12, 8, 12, 8)
        progress_layout.setSpacing(4)
        row = QHBoxLayout()
        self.phase = QLabel("Idle")
        self.phase.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 600;")
        self.elapsed = QLabel("")
        self.elapsed.setObjectName("Mono")
        self.cancel = QPushButton("Cancel")
        self.cancel.setObjectName("Tool")
        self.cancel.setEnabled(False)
        self.cancel.setVisible(False)
        self.cancel.clicked.connect(self.cancel_requested.emit)
        row.addWidget(self.phase, 1)
        row.addWidget(self.elapsed)
        row.addWidget(self.cancel)
        progress_layout.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        self.bar.hide()
        progress_layout.addWidget(self.bar)
        self.follow = QCheckBox("Follow build")
        self.follow.setChecked(True)
        self.follow.setStyleSheet(f"color: {COLORS['textSecondary']};")
        self.follow.toggled.connect(self.follow_changed.emit)
        progress_layout.addWidget(self.follow)
        layout.addWidget(self.progress)

        composer_frame = QFrame()
        composer_frame.setStyleSheet(f"background: {COLORS['shell']};"
                                     f"border-top: 1px solid {COLORS['border']};")
        composer_layout = QVBoxLayout(composer_frame)
        composer_layout.setContentsMargins(10, 8, 10, 8)
        composer_layout.setSpacing(6)
        self.composer = Composer()
        self.composer.submit.connect(self._send)
        composer_layout.addWidget(self.composer)
        row = QHBoxLayout()
        self.attach = QPushButton("Attach")
        self.attach.setObjectName("Tool")
        self.attach.clicked.connect(self.attach_requested.emit)
        self.send = QPushButton("Send")
        self.send.setObjectName("Primary")
        self.send.setMinimumWidth(88)
        self.send.clicked.connect(self._send)
        row.addWidget(self.attach)
        row.addStretch(1)
        row.addWidget(self.send)
        composer_layout.addLayout(row)
        layout.addWidget(composer_frame)

    # ---------------------------------------------------------------- messages
    def _at_bottom(self) -> bool:
        bar = self.scroll.verticalScrollBar()
        return bar.value() >= bar.maximum() - 8

    def _append(self, widget: QWidget, *, force_bottom: bool = False) -> None:
        stick = force_bottom or self._at_bottom()
        self.conversation_layout.insertWidget(self.conversation_layout.count() - 1, widget)
        if stick:
            self.new_activity.hide()
            QTimer.singleShot(0, self._scroll_bottom)
        else:
            self.new_activity.show()

    def _scroll_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _scroll_changed(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        if value >= bar.maximum() - 8:
            self.new_activity.hide()

    def scroll_to_latest(self) -> None:
        self._scroll_bottom()
        self.new_activity.hide()

    def add_system_note(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("Muted")
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {COLORS['textMuted']}; padding: 2px 4px;")
        self._append(label)

    def add_user_message(self, text: str) -> None:
        bubble = QFrame()
        bubble.setObjectName("Raised")
        bubble.setStyleSheet(
            f"QFrame {{ background: {COLORS['panelRaised']}; border: 1px solid "
            f"{COLORS['border']}; border-radius: 10px; }}")
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(10, 8, 10, 8)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)
        self._append(bubble, force_bottom=True)

    def add_assistant_summary(self, text: str) -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet(
            f"background: {COLORS['panel']}; border-left: 2px solid "
            f"{COLORS['accent']}; border-radius: 4px; padding: 8px 10px;")
        self._append(label)

    def stream_text(self, chunk: str) -> None:
        if not chunk:
            return
        stick = self._at_bottom()
        if not hasattr(self, "_stream_label") or self._stream_label is None:
            label = QLabel("")
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {COLORS['textSecondary']}; padding: 2px 4px;")
            self._append(label, force_bottom=True)
            self._stream_label = label
            self._stream_buffer = ""
        self._stream_buffer += chunk
        self._stream_label.setText(self._stream_buffer[-4000:])
        if stick:
            self._scroll_bottom()
        elif not self.new_activity.isVisible():
            self.new_activity.show()

    def finish_stream(self, final_text: str = "") -> None:
        if getattr(self, "_stream_label", None) is not None:
            if final_text:
                self._stream_label.setText(final_text[:6000])
            elif not self._stream_buffer.strip():
                self._stream_label.deleteLater()
        self._stream_label = None
        self._stream_buffer = ""

    def add_tool_card(self, call_id: str, tool: str,
                      arguments: dict[str, Any]) -> ActionCard:
        card = ActionCard(call_id, tool, arguments)
        self._append(card)
        return card

    def add_result_card(self, commit: dict[str, Any],
                        checks: dict[str, Any]) -> ResultCard:
        card = ResultCard(commit, checks)
        self._append(card)
        return card

    def add_error_card(self, message: str, code: str = "",
                       next_steps: list[str] | None = None) -> ErrorCard:
        card = ErrorCard(message, code, next_steps)
        self._append(card)
        return card

    def add_clarification(self, payload: dict[str, Any]) -> ClarificationCard:
        card = ClarificationCard(payload)
        card.submitted.connect(self.answer_submitted)
        self._append(card)
        return card

    def clear(self) -> None:
        while self.conversation_layout.count() > 1:
            item = self.conversation_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.phase.setText("Idle")
        self.elapsed.setText("")
        self.bar.hide()
        self.cancel.setEnabled(False)
        self.new_activity.hide()
        self._waiting_for_user = False

    # ------------------------------------------------------------------ status
    def set_model(self, model: str, configured: bool, label: str = "") -> None:
        name = f"{label} · {model}" if label else model
        if configured:
            self.model_label.setText(f"{name} · connected")
            self.model_label.setStyleSheet(f"color: {COLORS['success']};")
            self.model_label.setToolTip(f"Frozen per run: {name}")
        else:
            self.model_label.setText(f"{name} · no key")
            self.model_label.setStyleSheet(f"color: {COLORS['warning']};")
            self.model_label.setToolTip(
                "Template builds work without a model key. Add a key in AI connections "
                "to build from a description.")

    def set_template_mode(self) -> None:
        self.model_label.setText("Template build · local engine")
        self.model_label.setStyleSheet(f"color: {COLORS['textSecondary']};")

    def begin_run(self, label: str = "Starting build", can_cancel: bool = True) -> None:
        self.phase.setText(label)
        self.phase.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 600;")
        self.elapsed.setText("0:00")
        self._clock.restart()
        self._timer.start()
        self.bar.show()
        self.cancel.setVisible(can_cancel)
        self.cancel.setEnabled(can_cancel)
        self.new_activity.hide()
        self._waiting_for_user = False

    def set_state(self, state: str, label: str, message: str = "") -> None:
        self.phase.setText(label or state)
        if state == "WAITING_FOR_USER":
            self._waiting_for_user = True
            self._timer.stop()
            self.elapsed.setText("paused")
            self.bar.hide()
            self.cancel.setVisible(True)
            self.cancel.setEnabled(True)
        elif state in ("SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "RECOVERABLE"):
            self._timer.stop()
            self.bar.hide()
            self.cancel.setVisible(False)
            self.cancel.setEnabled(False)
            color = {
                "SUCCEEDED": COLORS["success"], "PARTIAL": COLORS["warning"],
                "FAILED": COLORS["error"], "CANCELLED": COLORS["textMuted"],
            }.get(state, COLORS["textSecondary"])
            self.phase.setStyleSheet(f"color: {color}; font-weight: 600;")
            self.phase.setText(label or state)
        else:
            self._waiting_for_user = False
            if not self._timer.isActive():
                self._clock.restart()
                self._timer.start()
            self.bar.show()
            self.cancel.setVisible(True)
            self.cancel.setEnabled(True)
            self.phase.setStyleSheet(f"color: {COLORS['accent']}; font-weight: 600;")

    def finish(self) -> None:
        self._timer.stop()
        self.bar.hide()
        self.cancel.setVisible(False)
        self.cancel.setEnabled(False)

    def _tick(self) -> None:
        if self._waiting_for_user:
            return
        seconds = self._clock.elapsed() // 1000
        self.elapsed.setText(f"{seconds // 60}:{seconds % 60:02d}")

    # -------------------------------------------------------------------- send
    def _send(self) -> None:
        text = self.composer.toPlainText().strip()
        if not text:
            return
        self.composer.clear()
        self.send_requested.emit(text)

    def set_send_enabled(self, enabled: bool, placeholder: str | None = None) -> None:
        self.send.setEnabled(enabled)
        self.composer.setEnabled(enabled)
        if placeholder:
            self.composer.setPlaceholderText(placeholder)

    def prefill(self, text: str) -> None:
        self.composer.setPlainText(text)
        cursor = self.composer.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.composer.setTextCursor(cursor)
        self.composer.setFocus()

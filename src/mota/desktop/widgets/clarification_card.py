"""Clarification question card (consumed exactly once)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QLabel, QLineEdit, QPushButton, QRadioButton,
    QVBoxLayout, QWidget,
)

from mota.desktop.theme.tokens import COLORS


class ClarificationCard(QFrame):
    submitted = Signal(str, dict)      # question_id, answer

    def __init__(self, payload: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Raised")
        self.payload = payload
        self.question_id = payload.get("question_id", "")
        self._consumed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        title = QLabel(payload.get("prompt", "Input required"))
        title.setObjectName("SectionTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        self.group = QButtonGroup(self)
        self.options: list[QRadioButton] = []
        for index, choice in enumerate(payload.get("choices") or []):
            radio = QRadioButton(f"{choice.get('label', '')} — "
                                 f"{choice.get('description', '')}")
            radio.setProperty("label", choice.get("label", ""))
            self.group.addButton(radio, index)
            self.options.append(radio)
            layout.addWidget(radio)
        fields = payload.get("fields") or []
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "Enter a value" + (f" for {', '.join(f.get('name', '') for f in fields)}"
                               if fields else ""))
        self.input.returnPressed.connect(self._submit)
        layout.addWidget(self.input)
        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {COLORS['error']};")
        self.error.hide()
        layout.addWidget(self.error)
        row = QVBoxLayout()
        self.button = QPushButton("Submit answer")
        self.button.setObjectName("Primary")
        self.button.clicked.connect(self._submit)
        row.addWidget(self.button)
        layout.addLayout(row)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        layout.addWidget(self.status)

    def _submit(self) -> None:
        if self._consumed:
            return
        choice = self.group.checkedButton()
        value = self.input.text().strip()
        if not choice and not value:
            self.error.setText("Choose an option or enter a value.")
            self.error.show()
            return
        answer: dict[str, Any] = {"value": value or (choice.property("label") if choice else "")}
        if choice:
            answer["choice"] = choice.property("label")
        self._consumed = True
        self.button.setEnabled(False)
        for radio in self.options:
            radio.setEnabled(False)
        self.input.setEnabled(False)
        self.status.setText("Answer sent · waiting for the run to continue")
        self.submitted.emit(self.question_id, answer)

    def mark_consumed(self) -> None:
        self._consumed = True
        self.button.setEnabled(False)
        for radio in self.options:
            radio.setEnabled(False)
        self.input.setEnabled(False)
        self.status.setText("Answered")

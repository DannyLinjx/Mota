"""Sign in / Create account page: fixed sample animation on the left, local form right."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from mota.desktop.theme.tokens import COLORS
from mota.desktop.widgets.brand import BrandMark


class SampleBoard(QWidget):
    """Deterministic sample animation, clearly separated from user projects."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.progress = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(12000)
        self.animation.setLoopCount(-1)
        self.animation.valueChanged.connect(self._update)
        self.reduced_motion = False
        self.components = [
            (0.18, 0.30, 0.10, 0.16), (0.42, 0.24, 0.16, 0.12),
            (0.68, 0.34, 0.08, 0.10), (0.30, 0.62, 0.12, 0.08),
            (0.58, 0.64, 0.14, 0.10), (0.78, 0.58, 0.07, 0.12),
        ]
        self.traces = [
            [(0.18, 0.38), (0.30, 0.38), (0.34, 0.34), (0.42, 0.34)],
            [(0.50, 0.30), (0.58, 0.30), (0.62, 0.36), (0.68, 0.39)],
            [(0.25, 0.36), (0.25, 0.55), (0.30, 0.60), (0.30, 0.66)],
            [(0.50, 0.38), (0.50, 0.58), (0.58, 0.64), (0.66, 0.64)],
            [(0.72, 0.39), (0.78, 0.44), (0.78, 0.58)],
        ]

    def _update(self, value) -> None:
        self.progress = float(value)
        self.update()

    def set_reduced_motion(self, reduced: bool) -> None:
        self.reduced_motion = reduced
        if reduced:
            self.animation.stop()
            self.progress = 1.0
            self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self.reduced_motion and self.animation.state() != QVariantAnimation.State.Running:
            self.animation.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.animation.pause()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0D151B"))
        width, height = self.width(), self.height()
        margin_x = width * 0.10
        margin_y = height * 0.16
        board = QRectF(margin_x, margin_y, width - 2 * margin_x, height - 2 * margin_y)
        progress = self.progress
        outline = min(1.0, progress * 4.0)
        painter.setPen(QPen(QColor(COLORS["boardOutline"]), 1.6))
        painter.setBrush(QColor(COLORS["board"]))
        if outline < 1.0:
            path = QRectF(board.x(), board.y(), board.width() * max(0.001, outline),
                          board.height() * max(0.001, outline))
            painter.drawRoundedRect(path, 8, 8)
            painter.end()
            return
        painter.drawRoundedRect(board, 8, 8)
        parts_alpha = max(0.0, min(1.0, (progress - 0.25) / 0.35))
        painter.setPen(QPen(QColor(COLORS["silk"]), 1.0))
        painter.setBrush(QColor(18, 45, 40, int(220 * parts_alpha)))
        for cx, cy, w, h in self.components:
            if parts_alpha <= 0:
                break
            rect = QRectF(board.x() + board.width() * cx,
                          board.y() + board.height() * cy,
                          board.width() * w, board.height() * h)
            painter.drawRoundedRect(rect, 2, 2)
        trace_progress = max(0.0, min(1.0, (progress - 0.55) / 0.35))
        if trace_progress > 0:
            painter.setPen(QPen(QColor(COLORS["fCu"]), 2.0,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for trace in self.traces:
                points = [QPointF(board.x() + board.width() * x,
                                  board.y() + board.height() * y) for x, y in trace]
                segments = len(points) - 1
                visible = trace_progress * segments
                for index in range(segments):
                    if visible <= index:
                        break
                    fraction = min(1.0, visible - index)
                    a = points[index]
                    b = points[index + 1]
                    painter.drawLine(a, QPointF(a.x() + (b.x() - a.x()) * fraction,
                                                a.y() + (b.y() - a.y()) * fraction))
        if progress > 0.9:
            painter.setPen(QPen(QColor(COLORS["bCu"]), 1.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(board.adjusted(8, 8, -8, -8), 4, 4)
        painter.end()


class AuthPage(QWidget):
    login_requested = Signal(str, str, bool)
    register_requested = Signal(str, str, str)
    back_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.reduced_motion = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        left = QFrame()
        left.setStyleSheet(f"background: {COLORS['shell']};")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(24, 20, 24, 20)
        brand_row = QHBoxLayout()
        back = QPushButton("← Welcome")
        back.setObjectName("Ghost")
        back.clicked.connect(self.back_requested.emit)
        brand_row.addWidget(back)
        brand_row.addStretch(1)
        brand_row.addWidget(BrandMark(22))
        left_layout.addLayout(brand_row)
        left_layout.addSpacing(24)
        self.sample = SampleBoard()
        self.sample.setMinimumHeight(320)
        left_layout.addWidget(self.sample, 1)
        title = QLabel("Build with intent.")
        title.setObjectName("H2")
        subtitle = QLabel("From requirements to a traceable design.")
        subtitle.setObjectName("Muted")
        left_layout.addWidget(title)
        left_layout.addWidget(subtitle)
        sample_note = QLabel("Sample circuit · not a user project")
        sample_note.setObjectName("Muted")
        sample_note.setAlignment(Qt.AlignmentFlag.AlignRight)
        left_layout.addWidget(sample_note)
        layout.addWidget(left, 56)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(40, 40, 40, 40)
        right_layout.addStretch(1)
        form_wrap = QHBoxLayout()
        form_wrap.addStretch(1)
        form = QWidget()
        form.setMaximumWidth(360)
        form.setMinimumWidth(300)
        form_layout = QVBoxLayout(form)
        form_layout.setSpacing(12)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_sign_in())
        self.stack.addWidget(self._build_register())
        form_layout.addWidget(self.stack)
        form_wrap.addWidget(form)
        form_wrap.addStretch(1)
        right_layout.addLayout(form_wrap)
        right_layout.addStretch(2)
        layout.addWidget(right, 44)

    # ------------------------------------------------------------------ forms
    def _build_sign_in(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        title = QLabel("Sign in")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        note = QLabel("Your projects stay in your local workspace.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.login_username = QLineEdit()
        self.login_username.setPlaceholderText("Username")
        self.login_password = QLineEdit()
        self.login_password.setPlaceholderText("Password")
        self.login_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.login_password.returnPressed.connect(self._submit_login)
        self.login_show = QPushButton("Show")
        self.login_show.setObjectName("Tool")
        self.login_show.setCheckable(True)
        self.login_show.toggled.connect(
            lambda checked: self.login_password.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        password_row = QHBoxLayout()
        password_row.addWidget(self.login_password, 1)
        password_row.addWidget(self.login_show)
        self.remember = QCheckBox("Remember me")
        self.login_error = QLabel("")
        self.login_error.setStyleSheet(f"color: {COLORS['error']};")
        self.login_error.setWordWrap(True)
        self.login_button = QPushButton("Sign in")
        self.login_button.setObjectName("Primary")
        self.login_button.clicked.connect(self._submit_login)
        self.switch_to_register = QPushButton("Create account")
        self.switch_to_register.setObjectName("Ghost")
        self.switch_to_register.clicked.connect(self.show_register_form)
        layout.addWidget(self.login_username)
        layout.addLayout(password_row)
        layout.addWidget(self.remember)
        layout.addWidget(self.login_error)
        layout.addWidget(self.login_button)
        layout.addWidget(self.switch_to_register)
        return page

    def _build_register(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        title = QLabel("Create account")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        note = QLabel("Your projects stay in your local workspace.")
        note.setObjectName("Muted")
        layout.addWidget(note)
        self.register_username = QLineEdit()
        self.register_username.setPlaceholderText("Username")
        self.register_display = QLineEdit()
        self.register_display.setPlaceholderText("Display name (optional)")
        self.register_password = QLineEdit()
        self.register_password.setPlaceholderText("Password")
        self.register_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.register_confirm = QLineEdit()
        self.register_confirm.setPlaceholderText("Confirm password")
        self.register_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.register_confirm.returnPressed.connect(self._submit_register)
        self.register_error = QLabel("")
        self.register_error.setStyleSheet(f"color: {COLORS['error']};")
        self.register_error.setWordWrap(True)
        self.register_button = QPushButton("Create account")
        self.register_button.setObjectName("Primary")
        self.register_button.clicked.connect(self._submit_register)
        self.switch_to_login = QPushButton("Back to sign in")
        self.switch_to_login.setObjectName("Ghost")
        self.switch_to_login.clicked.connect(self.show_login_form)
        for widget in (self.register_username, self.register_display,
                       self.register_password, self.register_confirm,
                       self.register_error, self.register_button, self.switch_to_login):
            layout.addWidget(widget)
        return page

    # ----------------------------------------------------------------- actions
    def _submit_login(self) -> None:
        username = self.login_username.text().strip()
        password = self.login_password.text()
        if not username or not password:
            self.show_error("Enter your username and password.")
            return
        self.login_requested.emit(username, password, self.remember.isChecked())

    def _submit_register(self) -> None:
        username = self.register_username.text().strip()
        password = self.register_password.text()
        if password != self.register_confirm.text():
            self.show_error("Passwords do not match.")
            return
        if len(password) < 8:
            self.show_error("Password must be at least 8 characters.")
            return
        self.register_requested.emit(username, password,
                                     self.register_display.text().strip())

    def set_busy(self, busy: bool, mode: str = "login") -> None:
        self.login_button.setText("Signing in…" if busy and mode == "login" else "Sign in")
        self.register_button.setText(
            "Creating account…" if busy and mode == "register" else "Create account")
        self.login_button.setEnabled(not busy)
        self.register_button.setEnabled(not busy)
        self.switch_to_register.setEnabled(not busy)
        self.switch_to_login.setEnabled(not busy)

    def show_login_form(self) -> None:
        if not self.login_button.isEnabled():
            return
        self.stack.setCurrentIndex(0)
        self.clear_error()

    def show_register_form(self) -> None:
        if not self.register_button.isEnabled():
            return
        self.stack.setCurrentIndex(1)
        self.clear_error()

    def show_error(self, text: str, mode: str = "login") -> None:
        label = self.login_error if mode == "login" else self.register_error
        label.setText(text)
        label.show()

    def clear_error(self) -> None:
        self.login_error.setText("")
        self.register_error.setText("")

    def set_reduced_motion(self, reduced: bool) -> None:
        self.reduced_motion = reduced
        self.sample.set_reduced_motion(reduced)

    def reset(self) -> None:
        self.stack.setCurrentIndex(0)
        self.login_password.clear()
        self.register_password.clear()
        self.register_confirm.clear()
        self.clear_error()
        self.set_busy(False, "login")

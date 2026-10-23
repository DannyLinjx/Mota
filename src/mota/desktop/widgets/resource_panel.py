"""Left resource panel: component search, inspector and templates."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QStackedWidget, QStyle,
    QStyledItemDelegate, QTextBrowser, QVBoxLayout, QWidget,
)

from mota.desktop.bridge import EngineBridge
from mota.desktop.theme.tokens import COLORS, DIMENSIONS


ROLE_ASSET = Qt.ItemDataRole.UserRole
ROLE_MPN = Qt.ItemDataRole.UserRole + 1
ROLE_SUBTITLE = Qt.ItemDataRole.UserRole + 2
ROLE_PLACEABLE = Qt.ItemDataRole.UserRole + 3
ROLE_BADGES = Qt.ItemDataRole.UserRole + 4


class PartDelegate(QStyledItemDelegate):
    """Two-line part row with a stable 56/40 DIP height."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.compact = False

    def set_compact(self, compact: bool) -> None:
        self.compact = compact

    def row_height(self) -> int:
        return DIMENSIONS["row_compact" if self.compact else "row"]

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(0, self.row_height())

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(2, 1, -2, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        placeable = index.data(ROLE_PLACEABLE)
        enabled = bool(placeable) if placeable is not None else True
        selectable = bool(index.flags() & Qt.ItemFlag.ItemIsSelectable)
        if not selectable:
            enabled = False
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["panelRaised"]))
            painter.drawRoundedRect(QRectF(rect), 7, 7)
        if selected:
            painter.setBrush(QColor(COLORS["accent"]))
            painter.drawRoundedRect(
                QRectF(rect.x() + 1, rect.y() + 6, 2.5, rect.height() - 12), 1.2, 1.2)
        mpn = str(index.data(ROLE_MPN) or index.data(Qt.ItemDataRole.DisplayRole) or "")
        subtitle = str(index.data(ROLE_SUBTITLE) or "")
        badges = str(index.data(ROLE_BADGES) or "")
        color = COLORS["textPrimary"] if enabled else COLORS["textMuted"]
        muted = COLORS["textSecondary"] if enabled else COLORS["textMuted"]
        left = rect.x() + 10
        width = rect.width() - 20
        if self.compact:
            top = rect.y() + 3
            font = QFont(option.font)
            font.setPixelSize(12)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(color))
            painter.drawText(QRectF(left, top, width, 16),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             mpn)
            font.setPixelSize(10)
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(QColor(muted))
            painter.drawText(QRectF(left, top + 16, width, 14),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             subtitle)
        else:
            top = rect.y() + 5
            font = QFont(option.font)
            font.setPixelSize(13)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(color))
            painter.drawText(QRectF(left, top, width, 17),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             mpn)
            font.setPixelSize(11)
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(QColor(muted))
            painter.drawText(QRectF(left, top + 18, width, 15),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             subtitle)
            if badges:
                painter.setPen(QColor(COLORS["accent"] if enabled else COLORS["textMuted"]))
                painter.drawText(QRectF(left, top + 33, width, 13),
                                 int(Qt.AlignmentFlag.AlignLeft |
                                     Qt.AlignmentFlag.AlignVCenter), badges)
        painter.restore()


class ResourcePanel(QFrame):
    add_part_requested = Signal(str)
    ask_part_requested = Signal(str, str)
    template_use_requested = Signal(dict)
    status_message = Signal(str)

    def __init__(self, bridge: EngineBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setMinimumWidth(220)
        self.setMaximumWidth(360)
        self.bridge = bridge
        self.offset = 0
        self.total = 0
        self.limit = 20
        self.selected_part: dict[str, Any] | None = None
        self.templates: list[dict[str, Any]] = []
        self._preview_seq = 0
        self._search_seq = 0
        self._edit_enabled = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        segment = QHBoxLayout()
        self.components_tab = QPushButton("Components")
        self.templates_tab = QPushButton("Templates")
        for button in (self.components_tab, self.templates_tab):
            button.setCheckable(True)
            button.setObjectName("Tab")
        self.components_tab.setChecked(True)
        self.components_tab.clicked.connect(lambda: self._switch(0))
        self.templates_tab.clicked.connect(lambda: self._switch(1))
        segment.addWidget(self.components_tab)
        segment.addWidget(self.templates_tab)
        layout.addLayout(segment)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_components())
        self.stack.addWidget(self._build_templates())

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._search)
        self.search.textChanged.connect(lambda: self.timer.start(240))
        self.category.currentTextChanged.connect(lambda: self._search())
        self.load_templates()
        QTimer.singleShot(0, self._run_search)

    def set_edit_enabled(self, enabled: bool) -> None:
        self._edit_enabled = enabled
        self.add_button.setEnabled(bool(enabled and self.selected_part and
                                        self.selected_part.get("placeable")))
        self.use_template.setEnabled(enabled and self.template_list.currentRow() >= 0)

    def set_compact_density(self, compact: bool) -> None:
        if self.part_delegate.compact == compact:
            return
        self.part_delegate.set_compact(compact)
        self.results.doItemsLayout()

    def refresh(self) -> None:
        self.load_templates()
        self._run_search()

    # ------------------------------------------------------------------ build
    def _build_components(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search parts, modules, or footprints")
        layout.addWidget(self.search)
        self.category = QComboBox()
        self.category.addItem("All types", "")
        for label, value in (("Components", "component"), ("Symbols", "symbol"),
                             ("Footprints", "footprint")):
            self.category.addItem(label, value)
        layout.addWidget(self.category)
        self.results = QListWidget()
        self.results.setWordWrap(False)
        self.results.setUniformItemSizes(True)
        self.results.setSpacing(0)
        self.results.setMouseTracking(True)
        self.part_delegate = PartDelegate(self.results)
        self.results.setItemDelegate(self.part_delegate)
        self.results.currentItemChanged.connect(self._on_result_selected)
        layout.addWidget(self.results, 2)
        footer = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setObjectName("Muted")
        self.next_button = QPushButton("More")
        self.next_button.setObjectName("Tool")
        self.next_button.clicked.connect(self._next_page)
        footer.addWidget(self.count_label, 1)
        footer.addWidget(self.next_button)
        layout.addLayout(footer)

        inspector_scroll = QScrollArea()
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setMinimumHeight(210)
        self.inspector = QTextBrowser()
        self.inspector.setOpenExternalLinks(False)
        self.inspector.setStyleSheet(
            f"QTextBrowser {{ background: {COLORS['panelRaised']};"
            f"border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 8px; }}")
        inspector_scroll.setWidget(self.inspector)
        layout.addWidget(inspector_scroll, 3)
        actions = QHBoxLayout()
        self.add_button = QPushButton("Add to design")
        self.add_button.setObjectName("Primary")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._add_part)
        self.ask_button = QPushButton("Ask Mota")
        self.ask_button.setObjectName("Tool")
        self.ask_button.setEnabled(False)
        self.ask_button.clicked.connect(self._ask_part)
        actions.addWidget(self.add_button)
        actions.addWidget(self.ask_button)
        layout.addLayout(actions)
        return page

    def _build_templates(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        caption = QLabel("Verified circuits are Mota recipes; project templates are "
                         "separate engineering starting points.")
        caption.setObjectName("Muted")
        caption.setWordWrap(True)
        layout.addWidget(caption)
        self.template_list = QListWidget()
        self.template_list.setWordWrap(True)
        self.template_list.currentRowChanged.connect(self._show_template)
        layout.addWidget(self.template_list, 2)
        self.template_detail = QTextBrowser()
        self.template_detail.setStyleSheet(
            f"QTextBrowser {{ background: {COLORS['panelRaised']};"
            f"border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 8px; }}")
        layout.addWidget(self.template_detail, 3)
        self.use_template = QPushButton("Use template")
        self.use_template.setObjectName("Primary")
        self.use_template.setEnabled(False)
        self.use_template.clicked.connect(self._use_template)
        layout.addWidget(self.use_template)
        return page

    # ----------------------------------------------------------------- actions
    def _switch(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.components_tab.setChecked(index == 0)
        self.templates_tab.setChecked(index == 1)

    def _search(self) -> None:
        self.offset = 0
        self._run_search()

    def _run_search(self) -> None:
        self._search_seq += 1
        seq = self._search_seq

        def done(result, error):
            if seq != self._search_seq:
                return
            self.results.clear()
            if error:
                self.count_label.setText("Catalog unavailable")
                item = QListWidgetItem(f"Catalog unavailable: {error.get('message', '')}")
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.results.addItem(item)
                return
            self.total = result.get("total", 0)
            self.count_label.setText(
                f"{len(result.get('candidates', []))} of {self.total} parts"
                if self.total else "No matching parts")
            for part in result.get("candidates", []):
                badges = " · ".join(part.get("capability_labels", []))
                item = QListWidgetItem()
                item.setData(ROLE_ASSET, part["asset_id"])
                item.setData(ROLE_MPN, part["mpn"])
                item.setData(ROLE_SUBTITLE, f"{part['package']} · {part['description'][:64]}")
                item.setData(ROLE_BADGES, badges)
                placeable = bool(part["capabilities"].get("placeable"))
                item.setData(ROLE_PLACEABLE, placeable)
                if not placeable:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.results.addItem(item)
            if self.total == 0 and self.search.text().strip():
                empty = QListWidgetItem("No matching parts")
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                self.results.addItem(empty)
            self.next_button.setEnabled(self.offset + self.limit < self.total)

        self.bridge.call("catalog.search", {
            "query": self.search.text(), "limit": self.limit, "offset": self.offset,
            "category": self.category.currentData() or None}, done)

    def _next_page(self) -> None:
        self.offset += self.limit
        self._run_search()

    def _on_result_selected(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        asset_id = current.data(ROLE_ASSET)
        if not asset_id:
            return
        self._preview_seq += 1
        seq = self._preview_seq
        self.selected_part = None
        self.add_button.setEnabled(False)
        self.ask_button.setEnabled(False)
        self.inspector.setHtml(
            f"<p style='color:{COLORS['textMuted']}'>Loading preview…</p>")

        def done(result, error):
            if seq != self._preview_seq:
                return  # A late reply must never overwrite the newer selection.
            if error:
                self.inspector.setHtml(f"<p style='color:{COLORS['error']}'>"
                                       f"{error.get('message', 'Part unavailable')}</p>")
                self.add_button.setEnabled(False)
                self.ask_button.setEnabled(False)
                return
            self.selected_part = result
            self.inspector.setHtml(self._part_html(result))
            # A symbol package carries real pins but no footprint binding; the
            # UI place path cannot guess one, so keep Place disabled until a
            # footprint is chosen. A component package already has bindings.
            needs_binding = (str(result.get("kind") or "") == "symbol"
                             and not result.get("footprint"))
            can_place = bool(result.get("placeable")) and not needs_binding
            self.add_button.setEnabled(can_place and
                                       bool(getattr(self, "_edit_enabled", False)))
            if needs_binding:
                self.add_button.setToolTip(
                    "Imported symbol: bind a compatible footprint first "
                    "(add_component footprint_id).")
            else:
                self.add_button.setToolTip(result.get("unsupported_reason") or "")
            self.ask_button.setEnabled(True)

        self.bridge.call("catalog.inspect", {"asset_id": asset_id}, done)

    def _part_html(self, data: dict[str, Any]) -> str:
        part = data["part"]
        capabilities = " · ".join(data.get("capability_labels", []))
        symbol = data.get("symbol") or {}
        footprint = data.get("footprint") or {}
        symbol_note = ""
        if symbol:
            symbol_note = (
                f" · symbol {symbol.get('symbol_id', '—')} "
                f"({symbol.get('body_w_mm', '?')} × {symbol.get('body_h_mm', '?')} mm)")
        footprint_note = (footprint.get("name") or footprint.get("footprint_id")
                          or "no footprint")
        rows = "".join(
            f"<tr><td style='color:{COLORS['textMuted']}'>{pin['number']}</td>"
            f"<td>{pin['name']}</td><td>{pin['electrical_type']}</td>"
            f"<td>{', '.join(pin['pad_numbers'])}</td></tr>"
            for pin in data["pins"])
        limits = "".join(f"<li>{limit['key']}: {limit['value']}</li>"
                         for limit in part.get("limits", []))
        sources = "".join(
            f"<li>{s['source_id']}: {s['title']}"
            + (f" — <a href='{s['url']}'>{s['url']}</a>" if s.get("url") else "")
            + "</li>" for s in data.get("sources", []))
        diagnostics = "".join(f"<li>{d['code']}: {d['detail']}</li>"
                              for d in data.get("diagnostics", []))
        return f"""
        <p style='font-size:15px;font-weight:600'>{part['mpn']}</p>
        <p style='color:{COLORS['textSecondary']}'>{part['description']}</p>
        <p><b>{part['package']}</b><br/><span style='color:{COLORS['textMuted']}'>
        footprint {footprint_note}{symbol_note}</span><br/>
        <span style='color:{COLORS['textMuted']}'>{capabilities}</span></p>
        <p style='color:{COLORS['textSecondary']}'>Pins / pads</p>
        <table width='100%' cellspacing='0' cellpadding='2'>{rows}</table>
        <p style='color:{COLORS['textSecondary']}'>Limits</p><ul>{limits or '<li>—</li>'}</ul>
        <p style='color:{COLORS['textSecondary']}'>Sources</p><ul>{sources or '<li>—</li>'}</ul>
        <p style='color:{COLORS['textSecondary']}'>Capability diagnostics</p>
        <ul>{diagnostics or '<li>none</li>'}</ul>
        """

    def _add_part(self) -> None:
        if self.selected_part:
            self.add_part_requested.emit(self.selected_part["part"]["asset_id"])

    def _ask_part(self) -> None:
        if self.selected_part:
            self.ask_part_requested.emit(self.selected_part["part"]["asset_id"],
                                         self.selected_part["part"]["mpn"])

    # --------------------------------------------------------------- templates
    def load_templates(self) -> None:
        def done(result, error):
            if error:
                self.template_list.addItem(f"Templates unavailable: "
                                           f"{error.get('message', '')}")
                return
            self.templates = result.get("templates", [])
            self.template_list.clear()
            for template in self.templates:
                board = template.get("board", {})
                item = QListWidgetItem(
                    f"{template['title']} · {template['status']}\n"
                    f"{template['subtitle']}\n"
                    f"{board.get('width_mm')}×{board.get('height_mm')} mm · "
                    f"{template['component_count']} parts · {template['net_count']} nets")
                self.template_list.addItem(item)
            if self.templates:
                self.template_list.setCurrentRow(0)

        self.bridge.call("template.list", {}, done)

    def _show_template(self, row: int) -> None:
        if row < 0 or row >= len(self.templates):
            self.use_template.setEnabled(False)
            return
        template = self.templates[row]
        parts = ", ".join(p["reference"] for p in template.get("parts", []))
        checks = ", ".join(template.get("required_checks", []))
        defaults = ", ".join(f"{k}={v}" for k, v in template.get("defaults", {}).items())
        self.template_detail.setHtml(f"""
        <p style='font-size:15px;font-weight:600'>{template['title']}</p>
        <p style='color:{COLORS['textSecondary']}'>{template['description']}</p>
        <p><b>Status</b> {template['status']} · <b>Version</b> {template['version']}</p>
        <p><b>Components</b> {parts}</p>
        <p><b>Defaults</b> {defaults}</p>
        <p><b>Required checks</b> {checks}</p>
        <p style='color:{COLORS['textMuted']}'>Use template creates a new project, then
        starts a build on its initial revision. It does not replace the open draft.</p>
        """)
        self.use_template.setEnabled(True)

    def _use_template(self) -> None:
        row = self.template_list.currentRow()
        if 0 <= row < len(self.templates):
            self.template_use_requested.emit(self.templates[row])

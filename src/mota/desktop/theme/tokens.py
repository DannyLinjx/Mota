"""Mota design tokens (MOTA-DEMO-1.0 §3)."""

from __future__ import annotations

COLORS = {
    "background": "#0B1015",
    "shell": "#10171D",
    "panel": "#141D25",
    "panelRaised": "#1A2630",
    "border": "#2B3B47",
    "textPrimary": "#E7EDF2",
    "textSecondary": "#A2B1BC",
    "textMuted": "#748895",
    "accent": "#5FDBC4",
    "accentText": "#06271F",
    "info": "#79B8F2",
    "success": "#82D4A1",
    "warning": "#F1C36D",
    "error": "#F07E8A",
    "selected": "#F4D35E",
    "draft": "#86BEE8",
    "schematicWire": "#B8D8AD",
    "schematicSymbol": "#E3C7A1",
    "boardOutline": "#D7E2EA",
    "fCu": "#D68C6A",
    "bCu": "#69A8E8",
    "ratsnest": "#C9B46E",
    "board": "#173E35",
    "silk": "#E0ECE7",
    "gridSmall": "#1C2A33",
    "gridLarge": "#2B3B47",
}

STATUS_COLORS = {
    "PASS": COLORS["success"],
    "FAIL": COLORS["error"],
    "WARN": COLORS["warning"],
    "NOT_CHECKED": COLORS["textMuted"],
    "READY_FOR_REVIEW": COLORS["success"],
    "REVIEW_BLOCKED": COLORS["warning"],
    "DRAFT": COLORS["draft"],
    "NEEDS_INPUT": COLORS["warning"],
    "SUCCEEDED": COLORS["success"],
    "PARTIAL": COLORS["warning"],
    "FAILED": COLORS["error"],
    "CANCELLED": COLORS["textMuted"],
    "RECOVERABLE": COLORS["warning"],
    "WAITING_FOR_USER": COLORS["info"],
}

DIMENSIONS = {
    "topbar": 64, "statusbar": 24, "left": 252, "right": 352,
    "splitter": 4, "view_tabs": 40, "toolbar": 36, "ruler": 22,
    "button": 36, "cta": 44, "input": 40, "icon_button": 32,
    "radius": 9, "card_radius": 13, "row": 56, "row_compact": 40,
}

SPACING = {"xs": 8, "sm": 12, "md": 16, "lg": 24, "xl": 32}

FONT_STACK = ('-apple-system, "Helvetica Neue", "Segoe UI", "Noto Sans", '
              '"PingFang SC", "Microsoft YaHei", sans-serif')
MONO_FONT = "SF Mono, Menlo, Consolas, monospace"
MONO_STACK = ('"SF Mono", Menlo, Consolas, "Liberation Mono", monospace')


def stylesheet(font_size: int = 14) -> str:
    c = COLORS
    return f"""
    * {{ outline: none; }}
    QWidget {{ background: {c['background']}; color: {c['textPrimary']};
               font-family: {FONT_STACK}; font-size: {font_size}px; }}
    QLabel {{ background: transparent; }}
    QCheckBox {{ background: transparent; }}
    QMainWindow, QDialog {{ background: {c['background']}; }}
    QFrame#Shell {{ background: {c['shell']}; }}
    QFrame#Panel, QWidget#Panel {{ background: {c['panel']}; }}
    QFrame#Raised {{ background: {c['panelRaised']}; border: 1px solid {c['border']};
                     border-radius: {DIMENSIONS['card_radius']}px; }}
    QFrame#Overlay {{ background: {c['panel']}; border: 1px solid {c['border']};
                      border-radius: {DIMENSIONS['card_radius']}px; }}
    QFrame#Soft {{ background: {c['panel']}; border: 1px solid {c['border']};
                   border-radius: {DIMENSIONS['radius']}px; }}
    QLabel#Muted {{ color: {c['textSecondary']}; }}
    QLabel#Mono {{ font-family: {MONO_STACK}; color: {c['textSecondary']}; }}
    QLabel#Metric {{ font-family: {MONO_STACK}; color: {c['textPrimary']}; }}
    QLabel#PageTitle {{ font-size: 22px; font-weight: 600; letter-spacing: 0.2px; }}
    QLabel#SectionTitle {{ font-size: 15px; font-weight: 600; }}
    QLabel#ToolTitle {{ font-size: 13px; font-weight: 600; }}
    QLabel#Brand {{ font-size: 20px; font-weight: 700; letter-spacing: 2px; }}
    QLabel#H1 {{ font-size: 44px; font-weight: 700; }}
    QLabel#H2 {{ font-size: 26px; font-weight: 600; }}
    QLabel#Chip {{ background: {c['panelRaised']}; border: 1px solid {c['border']};
                   border-radius: 8px; padding: 2px 7px; color: {c['textSecondary']};
                   font-size: 11px; }}
    QLabel#ChipAccent {{ background: rgba(95, 219, 196, 28); border: 1px solid
                         rgba(95, 219, 196, 105); border-radius: 8px; padding: 2px 7px;
                         color: {c['accent']}; font-size: 11px; }}
    QPushButton {{ background: transparent; border: 1px solid {c['border']};
                   border-radius: {DIMENSIONS['radius']}px; padding: 7px 14px;
                   min-height: {DIMENSIONS['button'] - 16}px; color: {c['textPrimary']}; }}
    QPushButton:hover {{ background: {c['panelRaised']}; border-color: #46606f; }}
    QPushButton:focus {{ border: 1px solid {c['accent']}; }}
    QPushButton:pressed {{ background: {c['panel']}; }}
    QPushButton:disabled {{ color: {c['textMuted']}; border-color: #223039;
                            background: transparent; }}
    QPushButton#Primary {{ background: {c['accent']}; color: {c['accentText']};
                          border: 1px solid transparent; font-weight: 600;
                          padding: 9px 18px;
                          min-height: {DIMENSIONS['cta'] - 18}px; }}
    QPushButton#Primary:hover {{ background: #79e6d1; }}
    QPushButton#Primary:focus {{ border: 2px solid #D6FFF6; }}
    QPushButton#Primary:pressed {{ background: #4cc6b0; }}
    QPushButton#Primary:disabled {{ background: #22403A; color: {c['textMuted']}; }}
    QPushButton#Ghost {{ border: none; color: {c['textSecondary']}; padding: 6px 8px; }}
    QPushButton#Ghost:hover {{ color: {c['accent']}; background: transparent; }}
    QPushButton#Tab {{ border: none; border-bottom: 2px solid transparent;
                       border-radius: 0; padding: 10px 14px; color: {c['textSecondary']}; }}
    QPushButton#Tab:hover {{ color: {c['textPrimary']}; background: transparent; }}
    QPushButton#Tab:checked {{ color: {c['textPrimary']}; border-bottom: 2px solid {c['accent']}; }}
    QPushButton#Tab:focus {{ border-bottom: 2px solid {c['accent']}; }}
    QPushButton#Tool {{ border: none; padding: 4px 9px; min-height: 24px;
                        border-radius: 7px; color: {c['textSecondary']}; }}
    QPushButton#Tool:hover {{ background: {c['panelRaised']}; color: {c['textPrimary']}; }}
    QPushButton#Tool:checked {{ background: {c['panelRaised']}; color: {c['accent']}; }}
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {c['panelRaised']}; border: 1px solid {c['border']};
        border-radius: {DIMENSIONS['radius']}px; padding: 8px 11px;
        selection-background-color: {c['info']}; selection-color: {c['background']}; }}
    QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QComboBox:hover {{
        border-color: #46606f; }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {c['accent']};
        background: #17242d; }}
    QLineEdit {{ min-height: {DIMENSIONS['input'] - 18}px; }}
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
    QDoubleSpinBox:disabled {{ color: {c['textMuted']}; background: #0F181E;
        border-color: #223039; }}
    QSpinBox QLineEdit, QDoubleSpinBox QLineEdit {{
        background: transparent; border: none; border-radius: 0; padding: 0;
        min-height: 0; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{ background: {c['panelRaised']};
        selection-background-color: {c['panel']}; border: 1px solid {c['border']};
        border-radius: 8px; padding: 4px; }}
    QListWidget, QTreeWidget, QTableWidget {{ background: {c['panel']};
        border: 1px solid {c['border']}; border-radius: {DIMENSIONS['radius']}px;
        padding: 4px; }}
    QListWidget::item {{ padding: 7px 9px; border-radius: 7px; margin: 1px 0;
                         color: {c['textPrimary']}; }}
    QListWidget::item:hover {{ background: {c['panelRaised']}; }}
    QListWidget::item:selected {{ background: {c['panelRaised']};
                                  border-left: 2px solid {c['accent']};
                                  color: {c['textPrimary']}; }}
    QListWidget::item:disabled {{ color: {c['textMuted']}; }}
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: #31444f; border-radius: 5px;
                                   min-height: 34px; }}
    QScrollBar::handle:vertical:hover {{ background: #45606e; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: #31444f; border-radius: 5px; }}
    QSplitter::handle {{ background: {c['background']}; }}
    QSplitter::handle:hover {{ background: {c['accent']}; }}
    QTabBar::tab {{ background: transparent; color: {c['textSecondary']};
                    padding: 8px 14px; border-bottom: 2px solid transparent; }}
    QTabBar::tab:selected {{ color: {c['textPrimary']};
                             border-bottom: 2px solid {c['accent']}; }}
    QMenu {{ background: {c['panelRaised']}; border: 1px solid {c['border']};
             border-radius: 10px; padding: 5px; }}
    QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {c['panel']}; }}
    QToolTip {{ background: {c['panelRaised']}; color: {c['textPrimary']};
                border: 1px solid {c['border']}; padding: 5px 8px; border-radius: 6px; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c['border']};
                            border-radius: 4px; background: {c['panelRaised']}; }}
    QCheckBox::indicator:hover {{ border-color: {c['accent']}; }}
    QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
    QProgressBar {{ background: {c['panelRaised']}; border: none; border-radius: 3px;
                    height: 5px; text-align: center; color: transparent; }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
    QStatusBar {{ background: {c['shell']}; color: {c['textSecondary']}; }}
    QStatusBar::item {{ border: none; }}
    """


from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeTokens:
    surface_app: str
    surface_menu: str
    surface_control: str
    surface_control_hover: str
    surface_control_alt: str
    text_primary: str
    text_muted: str
    border_default: str
    border_strong: str
    selection_bg: str
    selection_text: str
    success_bg: str
    success_border: str
    warning_bg: str
    warning_border: str
    error_bg: str
    error_border: str


DARK_TOKENS = ThemeTokens(
    surface_app="#1f1f1f",
    surface_menu="#252525",
    surface_control="#2a2a2a",
    surface_control_hover="#3d3d3d",
    surface_control_alt="#343434",
    text_primary="#ececec",
    text_muted="#b8b8b8",
    border_default="#454545",
    border_strong="#4a4a4a",
    selection_bg="#4f46e5",
    selection_text="#ffffff",
    success_bg="#14532d",
    success_border="#16a34a",
    warning_bg="#78350f",
    warning_border="#f59e0b",
    error_bg="#7f1d1d",
    error_border="#ef4444",
)

LIGHT_TOKENS = ThemeTokens(
    surface_app="#f4f6fb",
    surface_menu="#eef1f8",
    surface_control="#ffffff",
    surface_control_hover="#f2f4fa",
    surface_control_alt="#e7eaf3",
    text_primary="#111827",
    text_muted="#4b5563",
    border_default="#c9cfdd",
    border_strong="#b8c0d3",
    selection_bg="#4f46e5",
    selection_text="#ffffff",
    success_bg="#d1fae5",
    success_border="#10b981",
    warning_bg="#fff7d6",
    warning_border="#f59e0b",
    error_bg="#fee2e2",
    error_border="#ef4444",
)


def tokens_for_mode(mode: str) -> ThemeTokens:
    return DARK_TOKENS if (mode or "").strip().lower() == "dark" else LIGHT_TOKENS


def build_base_stylesheet(mode: str) -> str:
    t = tokens_for_mode(mode)
    return f"""
QWidget {{
    background-color: {t.surface_app};
    color: {t.text_primary};
}}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget, QTreeWidget, QTableWidget, QTabWidget::pane {{
    background-color: {t.surface_control};
    color: {t.text_primary};
    border: 1px solid {t.border_default};
    selection-background-color: {t.selection_bg};
    selection-color: {t.selection_text};
}}
QPushButton, QToolButton {{
    background-color: {t.surface_control_alt};
    color: {t.text_primary};
    border: 1px solid {t.border_default};
    border-radius: 6px;
    padding: 4px 8px;
}}
QPushButton:hover, QToolButton:hover {{
    background-color: {t.surface_control_hover};
}}
QMenuBar, QMenu {{
    background-color: {t.surface_menu};
    color: {t.text_primary};
}}
QMenu::item:selected {{
    background-color: {t.surface_control_hover};
}}
QGroupBox {{
    border: 1px solid {t.border_strong};
    margin-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px 0 4px;
}}
"""


def build_files_material_stylesheet(mode: str) -> str:
    t = tokens_for_mode(mode)
    return f"""
QWidget#files_tab_material_v1 {{
    background-color: {t.surface_app};
}}
QWidget#files_top_command_bar {{
    background-color: {t.surface_menu};
    border: 1px solid {t.border_default};
    border-radius: 10px;
}}
QPushButton#files_primary_action {{
    background-color: {t.selection_bg};
    color: {t.selection_text};
    border: 1px solid {t.selection_bg};
    font-weight: 600;
}}
QPushButton#files_primary_action:hover {{
    background-color: {t.selection_bg};
}}
QPushButton#files_tonal_action {{
    background-color: {t.surface_control_alt};
    color: {t.text_primary};
    border: 1px solid {t.border_strong};
}}
QLabel#files_path_label {{
    color: {t.text_muted};
    font-weight: 600;
}}
QLabel#files_chip {{
    border: 1px solid {t.border_default};
    border-radius: 999px;
    padding: 4px 10px;
    background-color: {t.surface_control};
    color: {t.text_primary};
    font-weight: 600;
}}
QLabel#files_chip[chipSeverity="info"] {{
    border-color: {t.border_default};
    background-color: {t.surface_control};
}}
QLabel#files_chip[chipSeverity="success"] {{
    border-color: {t.success_border};
    background-color: {t.success_bg};
}}
QLabel#files_chip[chipSeverity="warning"] {{
    border-color: {t.warning_border};
    background-color: {t.warning_bg};
}}
QLabel#files_chip[chipSeverity="error"] {{
    border-color: {t.error_border};
    background-color: {t.error_bg};
}}
QLabel#files_chip[chipSeverity="dirty"] {{
    border-color: {t.selection_bg};
    background-color: {t.surface_control_alt};
}}
QListWidget#files_generated_list::item:selected {{
    background-color: {t.selection_bg};
    color: {t.selection_text};
}}
QTableWidget#files_validation_table, QTableWidget#files_import_review_table {{
    gridline-color: {t.border_default};
    border: 1px solid {t.border_default};
}}
QTabWidget#files_view_tabs::pane {{
    border: 1px solid {t.border_default};
    border-radius: 8px;
}}
QTabBar::tab {{
    background-color: {t.surface_control_alt};
    border: 1px solid {t.border_default};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 10px;
    color: {t.text_primary};
}}
QTabBar::tab:selected {{
    background-color: {t.surface_control};
    font-weight: 600;
}}
"""

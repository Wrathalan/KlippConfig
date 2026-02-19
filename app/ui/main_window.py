
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import posixpath
import re
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.domain.models import Preset, ProjectConfig, RenderedPack, ValidationReport
from app.services.board_registry import get_board_profile, get_toolhead_board_profile
from app.services.exporter import ExportService
from app.services.printer_discovery import (
    DiscoveredPrinter,
    PrinterDiscoveryError,
    PrinterDiscoveryService,
)
from app.services.preset_catalog import PresetCatalogError, PresetCatalogService
from app.services.project_store import ProjectStoreService
from app.services.renderer import ConfigRenderService
from app.services.ssh_deploy import SSHDeployError, SSHDeployService
from app.services.ui_scaling import UIScaleMode, UIScalingService
from app.services.validator import ValidationService

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception:
    QWebEngineView = None


class PrinterControlWindow(QMainWindow):
    def __init__(self, initial_url: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Printer Control")
        self.resize(1280, 900)

        root = QWidget(self)
        layout = QVBoxLayout(root)

        controls = QHBoxLayout()
        self.url_edit = QLineEdit(root)
        self.url_edit.setText(initial_url)
        controls.addWidget(self.url_edit, 1)

        go_btn = QPushButton("Go", root)
        go_btn.clicked.connect(self._load_from_bar)
        controls.addWidget(go_btn)

        reload_btn = QPushButton("Reload", root)
        reload_btn.clicked.connect(self._reload)
        controls.addWidget(reload_btn)

        browser_btn = QPushButton("Open in Browser", root)
        browser_btn.clicked.connect(self._open_external)
        controls.addWidget(browser_btn)

        layout.addLayout(controls)

        if QWebEngineView is None:
            raise RuntimeError(
                "Embedded web control is unavailable (Qt WebEngine is not installed)."
            )
        self.web_view = QWebEngineView(root)
        layout.addWidget(self.web_view, 1)
        self.setCentralWidget(root)
        self._load_url(initial_url)

    @staticmethod
    def _normalize_url(raw_value: str) -> str:
        value = raw_value.strip()
        if not value:
            return ""
        if "://" not in value:
            value = f"http://{value}"
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            return ""
        return value

    def _load_from_bar(self) -> None:
        self._load_url(self.url_edit.text())

    def _load_url(self, raw_value: str) -> None:
        normalized = self._normalize_url(raw_value)
        if not normalized:
            return
        self.url_edit.setText(normalized)
        self.web_view.setUrl(QUrl(normalized))

    def _reload(self) -> None:
        self.web_view.reload()

    def _open_external(self) -> None:
        normalized = self._normalize_url(self.url_edit.text())
        if not normalized:
            return
        QDesktopServices.openUrl(QUrl(normalized))


class MainWindow(QMainWindow):
    MACRO_PACK_OPTIONS = {
        "core_maintenance": "Core Maintenance",
        "qgl_helpers": "QGL Helpers",
        "filament_ops": "Filament Ops",
    }

    ADDON_OPTIONS = {
        "ams_lite": "AMS Lite",
        "ercf_v2": "ERCF v2",
        "box_turtle": "Box Turtle",
        "trad_rack": "Trad Rack",
        "filament_buffer": "Filament Buffer",
    }

    DEFAULT_PROBE_TYPES = ["tap", "inductive", "bltouch", "klicky", "euclid"]
    UI_SCALE_OPTIONS: tuple[tuple[UIScaleMode, str], ...] = (
        ("auto", "Auto"),
        ("85", "85%"),
        ("90", "90%"),
        ("100", "100%"),
        ("110", "110%"),
        ("125", "125%"),
        ("150", "150%"),
    )

    def __init__(
        self,
        ui_scaling_service: UIScalingService | None = None,
        active_scale_mode: UIScaleMode | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("KlippConfig")
        self.resize(1380, 900)

        self.catalog_service = PresetCatalogService()
        self.render_service = ConfigRenderService()
        self.validation_service = ValidationService()
        self.export_service = ExportService()
        self.project_store = ProjectStoreService()
        self.ssh_service: SSHDeployService | None = None
        self.discovery_service = PrinterDiscoveryService()
        self.ui_scaling_service = ui_scaling_service or UIScalingService()
        self.active_scale_mode: UIScaleMode = self.ui_scaling_service.resolve_mode(
            saved=active_scale_mode or self.ui_scaling_service.load_mode()
        )
        self.ui_scale_actions: dict[UIScaleMode, QAction] = {}
        self.ui_scale_action_group: QActionGroup | None = None

        self.presets_by_id: dict[str, Preset] = {}
        self.current_preset: Preset | None = None
        self.current_project: ProjectConfig | None = None
        self.current_pack: RenderedPack | None = None
        self.current_report = ValidationReport()

        self._applying_project = False
        self._showing_external_file = False
        self.manage_current_remote_file: str | None = None
        self.manage_current_directory: str | None = None
        self.files_current_content: str = ""
        self.files_current_label: str = ""
        self.files_current_source: str = "generated"
        self.files_current_generated_name: str | None = None
        self.cfg_form_editors: list[dict[str, Any]] = []
        self._last_blocking_alert_snapshot: tuple[str, ...] = ()
        self.device_connected = False
        self.manage_control_windows: list[QMainWindow] = []

        self._build_ui()
        self._load_presets()
        self._render_and_validate()

    def _build_ui(self) -> None:
        self._build_menu()

        root = QWidget(self)
        root_layout = QVBoxLayout(root)

        self.conflict_alert_label = QLabel("", root)
        self.conflict_alert_label.setWordWrap(True)
        self.conflict_alert_label.setVisible(False)
        root_layout.addWidget(self.conflict_alert_label)

        self.tabs = QTabWidget(root)
        root_layout.addWidget(self.tabs)

        self.wizard_tab = self._build_wizard_tab()
        self.files_tab = self._build_files_tab()
        self.live_deploy_tab = self._build_live_deploy_tab()
        self.manage_printer_tab = self._build_manage_printer_tab()

        self.tabs.addTab(self.wizard_tab, "Configuration")
        self.tabs.addTab(self.files_tab, "Files")
        self.tabs.addTab(self.live_deploy_tab, "SSH")
        self.tabs.addTab(self.manage_printer_tab, "Manage Printer")

        self.setCentralWidget(root)
        self._build_footer_connection_health()
        self.statusBar().showMessage("Ready")

    def _build_footer_connection_health(self) -> None:
        status_bar = self.statusBar()
        self.device_health_caption = QLabel("Device", self)
        status_bar.addPermanentWidget(self.device_health_caption)
        self.device_health_icon = QLabel(self)
        self.device_health_icon.setFixedSize(12, 12)
        status_bar.addPermanentWidget(self.device_health_icon)
        self._set_device_connection_health(False, "No active SSH session.")

    def _set_device_connection_health(self, connected: bool, detail: str | None = None) -> None:
        self.device_connected = connected
        if not hasattr(self, "device_health_icon"):
            return
        color = "#16a34a" if connected else "#dc2626"
        state = "Connected" if connected else "Disconnected"
        self.device_health_icon.setStyleSheet(
            "QLabel {"
            f" background-color: {color};"
            " border: 1px solid #111827;"
            " border-radius: 6px;"
            "}"
        )
        tooltip = f"Device connection: {state}"
        if detail:
            tooltip = f"{tooltip}\n{detail}"
        self.device_health_icon.setToolTip(tooltip)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        new_action = QAction("New", self)
        new_action.triggered.connect(self._new_project)
        file_menu.addAction(new_action)

        load_action = QAction("Load Project...", self)
        load_action.triggered.connect(self._load_project_from_file)
        file_menu.addAction(load_action)

        save_action = QAction("Save Project...", self)
        save_action.triggered.connect(self._save_project_to_file)
        file_menu.addAction(save_action)

        file_menu.addSeparator()

        open_cfg_action = QAction("Open .cfg File...", self)
        open_cfg_action.triggered.connect(self._open_local_cfg_file)
        file_menu.addAction(open_cfg_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        tools_menu = self.menuBar().addMenu("&Tools")
        render_action = QAction("Render + Validate", self)
        render_action.triggered.connect(self._render_and_validate)
        tools_menu.addAction(render_action)

        view_menu = self.menuBar().addMenu("&View")
        self._build_ui_scale_menu(view_menu)

    def _build_ui_scale_menu(self, view_menu) -> None:
        scale_menu = view_menu.addMenu("UI Scale")
        action_group = QActionGroup(self)
        action_group.setExclusive(True)
        self.ui_scale_actions.clear()

        for mode, label in self.UI_SCALE_OPTIONS:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setActionGroup(action_group)
            action.setData(mode)
            action.triggered.connect(
                lambda checked, selected_mode=mode: self._on_ui_scale_selected(
                    selected_mode, checked
                )
            )
            scale_menu.addAction(action)
            self.ui_scale_actions[mode] = action

        self.ui_scale_action_group = action_group
        selected_mode = (
            self.active_scale_mode if self.active_scale_mode in self.ui_scale_actions else "auto"
        )
        self.ui_scale_actions[selected_mode].setChecked(True)

    def _on_ui_scale_selected(self, mode: UIScaleMode, checked: bool) -> None:
        if not checked:
            return

        app = QApplication.instance()
        if app is None:
            return

        selected_mode = self.ui_scaling_service.resolve_mode(saved=mode)
        self.ui_scaling_service.save_mode(selected_mode)
        self.ui_scaling_service.apply(app, selected_mode)
        self.active_scale_mode = selected_mode

        label = "Auto" if selected_mode == "auto" else f"{selected_mode}%"
        self.statusBar().showMessage(f"UI scale set to {label}", 2500)

    def _build_wizard_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        top_row = QHBoxLayout()
        self.render_validate_btn = QPushButton("Render + Validate", tab)
        self.render_validate_btn.clicked.connect(self._render_and_validate)
        top_row.addWidget(self.render_validate_btn)

        self.preset_notes_label = QLabel("", tab)
        self.preset_notes_label.setWordWrap(True)
        top_row.addWidget(self.preset_notes_label, 1)
        layout.addLayout(top_row)

        grid = QGridLayout()
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)

        wizard_group = QGroupBox("Core Hardware", tab)
        wizard_form = QFormLayout(wizard_group)

        self.preset_combo = QComboBox(wizard_group)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        wizard_form.addRow("Preset", self.preset_combo)

        self.board_combo = QComboBox(wizard_group)
        self.board_combo.currentIndexChanged.connect(self._render_and_validate)
        wizard_form.addRow("Mainboard", self.board_combo)

        self.dimension_x = QSpinBox(wizard_group)
        self.dimension_x.setRange(50, 1000)
        self.dimension_x.valueChanged.connect(self._render_and_validate)
        wizard_form.addRow("X size (mm)", self.dimension_x)

        self.dimension_y = QSpinBox(wizard_group)
        self.dimension_y.setRange(50, 1000)
        self.dimension_y.valueChanged.connect(self._render_and_validate)
        wizard_form.addRow("Y size (mm)", self.dimension_y)

        self.dimension_z = QSpinBox(wizard_group)
        self.dimension_z.setRange(50, 1000)
        self.dimension_z.valueChanged.connect(self._render_and_validate)
        wizard_form.addRow("Z size (mm)", self.dimension_z)

        self.probe_enabled_checkbox = QCheckBox("Enable probe", wizard_group)
        self.probe_enabled_checkbox.setObjectName("probe_enabled")
        self.probe_enabled_checkbox.toggled.connect(self._sync_probe_controls)
        self.probe_enabled_checkbox.toggled.connect(self._render_and_validate)
        wizard_form.addRow(self.probe_enabled_checkbox)

        self.probe_type_combo = QComboBox(wizard_group)
        self.probe_type_combo.setEditable(True)
        self.probe_type_combo.currentTextChanged.connect(self._render_and_validate)
        wizard_form.addRow("Probe type", self.probe_type_combo)

        self.toolhead_enabled_checkbox = QCheckBox("Enable CAN toolhead board", wizard_group)
        self.toolhead_enabled_checkbox.setObjectName("toolhead_enabled")
        self.toolhead_enabled_checkbox.toggled.connect(self._sync_toolhead_controls)
        self.toolhead_enabled_checkbox.toggled.connect(self._render_and_validate)
        wizard_form.addRow(self.toolhead_enabled_checkbox)

        self.toolhead_board_combo = QComboBox(wizard_group)
        self.toolhead_board_combo.currentIndexChanged.connect(self._render_and_validate)
        wizard_form.addRow("Toolhead board", self.toolhead_board_combo)

        self.toolhead_canbus_uuid_edit = QLineEdit(wizard_group)
        self.toolhead_canbus_uuid_edit.setPlaceholderText("replace-with-canbus-uuid")
        self.toolhead_canbus_uuid_edit.textChanged.connect(self._render_and_validate)
        wizard_form.addRow("CAN UUID", self.toolhead_canbus_uuid_edit)

        self.hotend_thermistor_edit = QLineEdit(wizard_group)
        self.hotend_thermistor_edit.setText("EPCOS 100K B57560G104F")
        self.hotend_thermistor_edit.textChanged.connect(self._render_and_validate)
        wizard_form.addRow("Hotend thermistor", self.hotend_thermistor_edit)

        self.bed_thermistor_edit = QLineEdit(wizard_group)
        self.bed_thermistor_edit.setText("EPCOS 100K B57560G104F")
        self.bed_thermistor_edit.textChanged.connect(self._render_and_validate)
        wizard_form.addRow("Bed thermistor", self.bed_thermistor_edit)

        grid.addWidget(wizard_group, 0, 0)

        options_group = QGroupBox("Macro Packs and Add-ons", tab)
        options_layout = QVBoxLayout(options_group)

        self.macros_group = QGroupBox("Macro Packs", options_group)
        macros_layout = QVBoxLayout(self.macros_group)
        self.macro_checkboxes: dict[str, QCheckBox] = {}
        for key, label in self.MACRO_PACK_OPTIONS.items():
            checkbox = QCheckBox(label, self.macros_group)
            checkbox.setObjectName(f"macro_{key}")
            checkbox.toggled.connect(
                lambda checked, name=key: self._on_macro_checkbox_toggled(name, checked)
            )
            macros_layout.addWidget(checkbox)
            self.macro_checkboxes[key] = checkbox
        options_layout.addWidget(self.macros_group)

        self.addons_group = QGroupBox("Add-ons", options_group)
        addons_layout = QVBoxLayout(self.addons_group)
        self.addon_checkboxes: dict[str, QCheckBox] = {}
        for key, label in self.ADDON_OPTIONS.items():
            checkbox = QCheckBox(label, self.addons_group)
            checkbox.setObjectName(f"addon_{key}")
            checkbox.toggled.connect(
                lambda checked, name=key: self._on_addon_checkbox_toggled(name, checked)
            )
            addons_layout.addWidget(checkbox)
            self.addon_checkboxes[key] = checkbox
        options_layout.addWidget(self.addons_group)

        self.led_group = QGroupBox("LED Control", options_group)
        led_form = QFormLayout(self.led_group)

        self.led_enabled_checkbox = QCheckBox("Enable status LEDs", self.led_group)
        self.led_enabled_checkbox.setObjectName("led_enabled")
        self.led_enabled_checkbox.toggled.connect(self._sync_led_controls)
        self.led_enabled_checkbox.toggled.connect(self._render_and_validate)
        led_form.addRow(self.led_enabled_checkbox)

        self.led_pin_edit = QLineEdit(self.led_group)
        self.led_pin_edit.setPlaceholderText("PA8 or toolhead:RGB")
        self.led_pin_edit.setText("PA8")
        self.led_pin_edit.textChanged.connect(self._render_and_validate)
        led_form.addRow("LED pin", self.led_pin_edit)

        self.led_chain_count_spin = QSpinBox(self.led_group)
        self.led_chain_count_spin.setRange(1, 256)
        self.led_chain_count_spin.setValue(1)
        self.led_chain_count_spin.valueChanged.connect(self._render_and_validate)
        led_form.addRow("Chain count", self.led_chain_count_spin)

        self.led_color_order_combo = QComboBox(self.led_group)
        self.led_color_order_combo.addItems(["GRB", "RGB", "BRG", "BGR"])
        self.led_color_order_combo.currentTextChanged.connect(self._render_and_validate)
        led_form.addRow("Color order", self.led_color_order_combo)

        self.led_initial_red_spin = QDoubleSpinBox(self.led_group)
        self.led_initial_red_spin.setRange(0.0, 1.0)
        self.led_initial_red_spin.setDecimals(2)
        self.led_initial_red_spin.setSingleStep(0.05)
        self.led_initial_red_spin.setValue(0.0)
        self.led_initial_red_spin.valueChanged.connect(self._render_and_validate)
        led_form.addRow("Initial red", self.led_initial_red_spin)

        self.led_initial_green_spin = QDoubleSpinBox(self.led_group)
        self.led_initial_green_spin.setRange(0.0, 1.0)
        self.led_initial_green_spin.setDecimals(2)
        self.led_initial_green_spin.setSingleStep(0.05)
        self.led_initial_green_spin.setValue(0.0)
        self.led_initial_green_spin.valueChanged.connect(self._render_and_validate)
        led_form.addRow("Initial green", self.led_initial_green_spin)

        self.led_initial_blue_spin = QDoubleSpinBox(self.led_group)
        self.led_initial_blue_spin.setRange(0.0, 1.0)
        self.led_initial_blue_spin.setDecimals(2)
        self.led_initial_blue_spin.setSingleStep(0.05)
        self.led_initial_blue_spin.setValue(0.0)
        self.led_initial_blue_spin.valueChanged.connect(self._render_and_validate)
        led_form.addRow("Initial blue", self.led_initial_blue_spin)

        options_layout.addWidget(self.led_group)

        self.board_summary = QPlainTextEdit(options_group)
        self.board_summary.setReadOnly(True)
        self.board_summary.setPlaceholderText("Board details and connector groups appear here.")
        options_layout.addWidget(self.board_summary, 1)

        grid.addWidget(options_group, 0, 1)
        layout.addLayout(grid)
        return tab

    def _build_files_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        top_row = QHBoxLayout()

        open_local_btn = QPushButton("Open Local .cfg", tab)
        open_local_btn.clicked.connect(self._open_local_cfg_file)
        top_row.addWidget(open_local_btn)

        show_generated_btn = QPushButton("Show Generated Files", tab)
        show_generated_btn.clicked.connect(self._show_selected_generated_file)
        top_row.addWidget(show_generated_btn)

        self.apply_form_btn = QPushButton("Apply Form Changes", tab)
        self.apply_form_btn.clicked.connect(self._apply_cfg_form_changes)
        top_row.addWidget(self.apply_form_btn)

        self.preview_path_label = QLabel("No file selected.", tab)
        top_row.addWidget(self.preview_path_label, 1)

        layout.addLayout(top_row)

        self.files_validation_notice_label = QLabel("", tab)
        self.files_validation_notice_label.setWordWrap(True)
        self.files_validation_notice_label.setVisible(False)
        layout.addWidget(self.files_validation_notice_label)

        splitter = QSplitter(Qt.Horizontal, tab)
        self.generated_file_list = QListWidget(splitter)
        self.generated_file_list.itemSelectionChanged.connect(self._on_generated_file_selected)

        right_panel = QWidget(splitter)
        right_layout = QVBoxLayout(right_panel)

        self.file_view_tabs = QTabWidget(right_panel)
        right_layout.addWidget(self.file_view_tabs, 1)

        text_tab = QWidget(self.file_view_tabs)
        text_layout = QVBoxLayout(text_tab)
        self.file_preview = QPlainTextEdit(text_tab)
        self.file_preview.setReadOnly(True)
        text_layout.addWidget(self.file_preview)
        self.file_view_tabs.addTab(text_tab, "Raw")

        form_tab = QWidget(self.file_view_tabs)
        form_layout = QVBoxLayout(form_tab)

        self.form_summary_label = QLabel("Select a .cfg file to build editable fields.", form_tab)
        self.form_summary_label.setWordWrap(True)
        form_layout.addWidget(self.form_summary_label)

        self.form_scroll = QScrollArea(form_tab)
        self.form_scroll.setWidgetResizable(True)
        self.form_container = QWidget(self.form_scroll)
        self.form_container_layout = QVBoxLayout(self.form_container)
        self.form_container_layout.setContentsMargins(0, 0, 0, 0)
        self.form_container_layout.setSpacing(8)
        self.form_container_layout.addStretch(1)
        self.form_scroll.setWidget(self.form_container)
        form_layout.addWidget(self.form_scroll, 1)
        self.file_view_tabs.addTab(form_tab, "Form")

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        (
            overrides_section,
            self.overrides_section_toggle,
            self.overrides_section_content,
            overrides_layout,
        ) = self._build_collapsible_section("Section Overrides", tab, expanded=False)

        description = QLabel(
            "Advanced overrides for generated output. Keys can use forms like "
            "'motion.max_velocity', 'mcu.serial', or 'pins.stepper_x_step'.",
            self.overrides_section_content,
        )
        description.setWordWrap(True)
        overrides_layout.addWidget(description)

        self.overrides_table = QTableWidget(self.overrides_section_content)
        self.overrides_table.setColumnCount(2)
        self.overrides_table.setHorizontalHeaderLabels(["Key", "Value"])
        self.overrides_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.overrides_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.overrides_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.overrides_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.overrides_table.itemChanged.connect(self._render_and_validate)
        overrides_layout.addWidget(self.overrides_table)

        button_row = QHBoxLayout()

        add_btn = QPushButton("Add Override", self.overrides_section_content)
        add_btn.clicked.connect(self._add_override_row)
        button_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove Selected", self.overrides_section_content)
        remove_btn.clicked.connect(self._remove_selected_override_rows)
        button_row.addWidget(remove_btn)

        clear_btn = QPushButton("Clear Overrides", self.overrides_section_content)
        clear_btn.clicked.connect(self._clear_overrides)
        button_row.addWidget(clear_btn)

        button_row.addStretch(1)

        validate_btn = QPushButton("Render + Validate", self.overrides_section_content)
        validate_btn.clicked.connect(self._render_and_validate)
        button_row.addWidget(validate_btn)

        overrides_layout.addLayout(button_row)
        layout.addWidget(overrides_section)

        (
            validation_section,
            self.validation_section_toggle,
            self.validation_section_content,
            validation_layout,
        ) = self._build_collapsible_section("Validation Findings", tab, expanded=False)

        self.validation_status_label = QLabel("No validation run yet.", self.validation_section_content)
        self.validation_status_label.setWordWrap(True)
        validation_layout.addWidget(self.validation_status_label)

        self.validation_table = QTableWidget(self.validation_section_content)
        self.validation_table.setColumnCount(4)
        self.validation_table.setHorizontalHeaderLabels(["Severity", "Code", "Field", "Message"])
        self.validation_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.validation_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.validation_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.validation_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.validation_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.validation_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.validation_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        validation_layout.addWidget(self.validation_table)

        rerun_btn = QPushButton("Re-run Validation", self.validation_section_content)
        rerun_btn.clicked.connect(self._render_and_validate)
        validation_layout.addWidget(rerun_btn)

        layout.addWidget(validation_section)

        export_group = QGroupBox("Export", tab)
        export_layout = QVBoxLayout(export_group)
        intro = QLabel(
            "Export creates a Klipper config pack for manual upload or direct SSH deployment.",
            export_group,
        )
        intro.setWordWrap(True)
        export_layout.addWidget(intro)

        button_row = QHBoxLayout()
        self.export_folder_btn = QPushButton("Export Folder", export_group)
        self.export_folder_btn.clicked.connect(self._export_folder)
        button_row.addWidget(self.export_folder_btn)

        self.export_zip_btn = QPushButton("Export ZIP", export_group)
        self.export_zip_btn.clicked.connect(self._export_zip)
        button_row.addWidget(self.export_zip_btn)

        save_project_btn = QPushButton("Save Project", export_group)
        save_project_btn.clicked.connect(self._save_project_to_file)
        button_row.addWidget(save_project_btn)

        load_project_btn = QPushButton("Load Project", export_group)
        load_project_btn.clicked.connect(self._load_project_from_file)
        button_row.addWidget(load_project_btn)

        button_row.addStretch(1)
        export_layout.addLayout(button_row)

        self.export_status_label = QLabel("No export performed.", export_group)
        self.export_status_label.setWordWrap(True)
        export_layout.addWidget(self.export_status_label)

        layout.addWidget(export_group)
        return tab

    def _build_collapsible_section(
        self,
        title: str,
        parent: QWidget,
        expanded: bool = False,
    ) -> tuple[QWidget, QToolButton, QWidget, QVBoxLayout]:
        container = QWidget(parent)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(4)

        toggle = QToolButton(container)
        toggle.setText(title)
        toggle.setCheckable(True)
        toggle.setChecked(expanded)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        toggle.setStyleSheet("QToolButton { font-weight: 600; }")
        container_layout.addWidget(toggle)

        content = QWidget(container)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 0, 0, 0)
        content_layout.setSpacing(6)
        content.setVisible(expanded)
        container_layout.addWidget(content)

        def _on_toggled(checked: bool) -> None:
            toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
            content.setVisible(checked)

        toggle.toggled.connect(_on_toggled)
        return container, toggle, content, content_layout

    def _build_live_deploy_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        connection_group = QGroupBox("SSH Connection", tab)
        connection_form = QFormLayout(connection_group)

        self.ssh_host_edit = QLineEdit(connection_group)
        self.ssh_host_edit.setPlaceholderText("printer.local")
        connection_form.addRow("Host", self.ssh_host_edit)

        self.ssh_port_spin = QSpinBox(connection_group)
        self.ssh_port_spin.setRange(1, 65535)
        self.ssh_port_spin.setValue(22)
        connection_form.addRow("Port", self.ssh_port_spin)

        self.ssh_username_edit = QLineEdit(connection_group)
        self.ssh_username_edit.setPlaceholderText("pi")
        connection_form.addRow("Username", self.ssh_username_edit)

        self.ssh_password_edit = QLineEdit(connection_group)
        self.ssh_password_edit.setEchoMode(QLineEdit.Password)
        connection_form.addRow("Password", self.ssh_password_edit)

        key_row = QWidget(connection_group)
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)

        self.ssh_key_path_edit = QLineEdit(key_row)
        self.ssh_key_path_edit.setPlaceholderText("C:/Users/<you>/.ssh/id_ed25519")
        key_layout.addWidget(self.ssh_key_path_edit, 1)

        browse_key_btn = QPushButton("Browse", key_row)
        browse_key_btn.clicked.connect(self._browse_ssh_key)
        key_layout.addWidget(browse_key_btn)
        connection_form.addRow("SSH key", key_row)

        self.ssh_remote_dir_edit = QLineEdit(connection_group)
        self.ssh_remote_dir_edit.setText("~/printer_data/config")
        connection_form.addRow("Remote cfg dir", self.ssh_remote_dir_edit)

        self.ssh_remote_fetch_path_edit = QLineEdit(connection_group)
        self.ssh_remote_fetch_path_edit.setText("~/printer_data/config/printer.cfg")
        connection_form.addRow("Remote file", self.ssh_remote_fetch_path_edit)

        layout.addWidget(connection_group)

        discovery_group = QGroupBox("Printer Discovery", tab)
        discovery_layout = QVBoxLayout(discovery_group)
        discovery_form = QFormLayout()

        suggested_cidrs = self.discovery_service.suggest_scan_cidrs()
        self.scan_cidr_edit = QLineEdit(discovery_group)
        self.scan_cidr_edit.setPlaceholderText("192.168.1.0/24")
        self.scan_cidr_edit.setText(suggested_cidrs[0] if suggested_cidrs else "192.168.1.0/24")
        discovery_form.addRow("CIDR range", self.scan_cidr_edit)

        self.scan_timeout_spin = QDoubleSpinBox(discovery_group)
        self.scan_timeout_spin.setRange(0.05, 3.0)
        self.scan_timeout_spin.setDecimals(2)
        self.scan_timeout_spin.setSingleStep(0.05)
        self.scan_timeout_spin.setValue(0.35)
        discovery_form.addRow("Timeout (s)", self.scan_timeout_spin)

        self.scan_max_hosts_spin = QSpinBox(discovery_group)
        self.scan_max_hosts_spin.setRange(1, 4096)
        self.scan_max_hosts_spin.setValue(254)
        discovery_form.addRow("Max hosts", self.scan_max_hosts_spin)
        discovery_layout.addLayout(discovery_form)

        discovery_buttons = QHBoxLayout()
        self.scan_network_btn = QPushButton("Scan for Printers", discovery_group)
        self.scan_network_btn.clicked.connect(self._scan_for_printers)
        discovery_buttons.addWidget(self.scan_network_btn)

        self.use_scanned_btn = QPushButton("Use Selected Host", discovery_group)
        self.use_scanned_btn.clicked.connect(self._use_selected_discovery_host)
        discovery_buttons.addWidget(self.use_scanned_btn)
        discovery_buttons.addStretch(1)
        discovery_layout.addLayout(discovery_buttons)

        self.discovery_results_table = QTableWidget(discovery_group)
        self.discovery_results_table.setColumnCount(4)
        self.discovery_results_table.setHorizontalHeaderLabels(
            ["Host", "Moonraker", "SSH", "Details"]
        )
        self.discovery_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.discovery_results_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.discovery_results_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.discovery_results_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.discovery_results_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self.discovery_results_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self.discovery_results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.discovery_results_table.cellDoubleClicked.connect(
            lambda _row, _col: self._use_selected_discovery_host()
        )
        discovery_layout.addWidget(self.discovery_results_table, 1)

        layout.addWidget(discovery_group)

        options_group = QGroupBox("Deploy Options", tab)
        options_form = QFormLayout(options_group)

        self.ssh_backup_checkbox = QCheckBox("Backup remote config before upload", options_group)
        self.ssh_backup_checkbox.setChecked(True)
        options_form.addRow(self.ssh_backup_checkbox)

        self.ssh_restart_checkbox = QCheckBox("Restart Klipper after upload", options_group)
        self.ssh_restart_checkbox.setChecked(False)
        options_form.addRow(self.ssh_restart_checkbox)

        self.ssh_restart_cmd_edit = QLineEdit(options_group)
        self.ssh_restart_cmd_edit.setText("sudo systemctl restart klipper")
        options_form.addRow("Restart command", self.ssh_restart_cmd_edit)
        layout.addWidget(options_group)

        button_row = QHBoxLayout()

        test_btn = QPushButton("Test Connection", tab)
        test_btn.clicked.connect(self._test_ssh_connection)
        button_row.addWidget(test_btn)

        fetch_btn = QPushButton("Open Remote File", tab)
        fetch_btn.clicked.connect(self._fetch_remote_cfg_file)
        button_row.addWidget(fetch_btn)

        self.deploy_btn = QPushButton("Deploy Generated Pack", tab)
        self.deploy_btn.clicked.connect(self._deploy_generated_pack)
        button_row.addWidget(self.deploy_btn)

        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.ssh_log = QPlainTextEdit(tab)
        self.ssh_log.setReadOnly(True)
        layout.addWidget(self.ssh_log, 1)
        return tab

    def _build_manage_printer_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        intro = QLabel(
            "Manage a connected printer directly over SSH: browse/edit files, create backups, and restore backups.",
            tab,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        target_group = QGroupBox("Target Printer", tab)
        target_form = QFormLayout(target_group)

        host_row = QWidget(target_group)
        host_row_layout = QHBoxLayout(host_row)
        host_row_layout.setContentsMargins(0, 0, 0, 0)
        self.manage_host_edit = QLineEdit(host_row)
        self.manage_host_edit.setPlaceholderText("leave empty to use SSH host")
        host_row_layout.addWidget(self.manage_host_edit, 1)

        use_ssh_host_btn = QPushButton("Use SSH Host", host_row)
        use_ssh_host_btn.clicked.connect(self._use_ssh_host_for_manage)
        host_row_layout.addWidget(use_ssh_host_btn)
        target_form.addRow("Host", host_row)

        self.manage_control_url_edit = QLineEdit(target_group)
        self.manage_control_url_edit.setPlaceholderText(
            "optional; defaults to host (for example: http://printer.local/)"
        )
        target_form.addRow("Control URL", self.manage_control_url_edit)

        self.manage_remote_dir_edit = QLineEdit(target_group)
        self.manage_remote_dir_edit.setText(self.ssh_remote_dir_edit.text().strip())
        target_form.addRow("Remote cfg dir", self.manage_remote_dir_edit)

        self.manage_backup_root_edit = QLineEdit(target_group)
        self.manage_backup_root_edit.setText("~/klippconfig_backups")
        target_form.addRow("Backup root", self.manage_backup_root_edit)

        self.manage_scan_depth_spin = QSpinBox(target_group)
        self.manage_scan_depth_spin.setRange(1, 10)
        self.manage_scan_depth_spin.setValue(5)
        target_form.addRow("File scan depth", self.manage_scan_depth_spin)

        layout.addWidget(target_group)

        action_row = QHBoxLayout()
        self.manage_refresh_files_btn = QPushButton("Refresh Files", tab)
        self.manage_refresh_files_btn.clicked.connect(self._manage_refresh_files)
        action_row.addWidget(self.manage_refresh_files_btn)

        self.manage_up_dir_btn = QPushButton("Up Directory", tab)
        self.manage_up_dir_btn.clicked.connect(self._manage_browse_up_directory)
        action_row.addWidget(self.manage_up_dir_btn)

        self.manage_open_file_btn = QPushButton("Open Selected / Enter Folder", tab)
        self.manage_open_file_btn.clicked.connect(self._manage_open_selected_file)
        action_row.addWidget(self.manage_open_file_btn)

        self.manage_save_file_btn = QPushButton("Save Current File", tab)
        self.manage_save_file_btn.clicked.connect(self._manage_save_current_file)
        action_row.addWidget(self.manage_save_file_btn)

        self.manage_open_control_btn = QPushButton("Open Control Window", tab)
        self.manage_open_control_btn.clicked.connect(self._manage_open_control_window)
        action_row.addWidget(self.manage_open_control_btn)

        action_row.addStretch(1)
        layout.addLayout(action_row)

        editor_splitter = QSplitter(Qt.Horizontal, tab)
        self.manage_file_list = QListWidget(editor_splitter)
        self.manage_file_list.itemSelectionChanged.connect(self._manage_file_selection_changed)
        self.manage_file_list.itemDoubleClicked.connect(lambda _item: self._manage_open_selected_file())

        editor_panel = QWidget(editor_splitter)
        editor_layout = QVBoxLayout(editor_panel)
        self.manage_current_dir_label = QLabel("Browsing: (not loaded)", editor_panel)
        editor_layout.addWidget(self.manage_current_dir_label)
        self.manage_current_file_label = QLabel("No file loaded.", editor_panel)
        editor_layout.addWidget(self.manage_current_file_label)
        self.manage_file_editor = QPlainTextEdit(editor_panel)
        editor_layout.addWidget(self.manage_file_editor, 1)

        editor_splitter.setStretchFactor(0, 1)
        editor_splitter.setStretchFactor(1, 3)
        layout.addWidget(editor_splitter, 1)

        backup_group = QGroupBox("Backups", tab)
        backup_layout = QVBoxLayout(backup_group)

        backup_buttons = QHBoxLayout()
        self.manage_create_backup_btn = QPushButton("Create Backup", backup_group)
        self.manage_create_backup_btn.clicked.connect(self._manage_create_backup)
        backup_buttons.addWidget(self.manage_create_backup_btn)

        self.manage_refresh_backups_btn = QPushButton("Refresh Backups", backup_group)
        self.manage_refresh_backups_btn.clicked.connect(self._manage_refresh_backups)
        backup_buttons.addWidget(self.manage_refresh_backups_btn)

        backup_buttons.addStretch(1)
        backup_layout.addLayout(backup_buttons)

        backup_select_row = QHBoxLayout()
        self.manage_backup_combo = QComboBox(backup_group)
        backup_select_row.addWidget(self.manage_backup_combo, 1)
        self.manage_clear_before_restore_checkbox = QCheckBox(
            "Clear config dir before restore", backup_group
        )
        self.manage_clear_before_restore_checkbox.setChecked(True)
        backup_select_row.addWidget(self.manage_clear_before_restore_checkbox)
        self.manage_restore_backup_btn = QPushButton("Restore Selected Backup", backup_group)
        self.manage_restore_backup_btn.clicked.connect(self._manage_restore_selected_backup)
        backup_select_row.addWidget(self.manage_restore_backup_btn)
        backup_layout.addLayout(backup_select_row)

        self.manage_download_backup_btn = QPushButton(
            "Download Selected Backup to Desktop", backup_group
        )
        self.manage_download_backup_btn.clicked.connect(self._manage_download_selected_backup)
        backup_layout.addWidget(self.manage_download_backup_btn)

        layout.addWidget(backup_group)

        self.manage_log = QPlainTextEdit(tab)
        self.manage_log.setReadOnly(True)
        self.manage_log.setMaximumBlockCount(1000)
        layout.addWidget(self.manage_log, 1)

        self.ssh_remote_dir_edit.textChanged.connect(self._sync_manage_remote_dir_from_ssh)
        return tab

    def _load_presets(self) -> None:
        try:
            summaries = self.catalog_service.list_presets()
        except PresetCatalogError as exc:
            self._show_error("Preset Load Error", str(exc))
            return

        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.presets_by_id.clear()
        for summary in summaries:
            self.preset_combo.addItem(summary.name, summary.id)
            self.presets_by_id[summary.id] = self.catalog_service.load_preset(summary.id)
        self.preset_combo.blockSignals(False)

        if self.preset_combo.count() > 0:
            self.preset_combo.setCurrentIndex(0)
            self._on_preset_changed(0)

    def _on_preset_changed(self, _: int) -> None:
        preset_id = self.preset_combo.currentData()
        if not isinstance(preset_id, str):
            return

        preset = self.presets_by_id.get(preset_id)
        if preset is None:
            return

        self.current_preset = preset
        self.preset_notes_label.setText(preset.notes or "")

        self._populate_board_combo(preset.supported_boards)
        self._populate_toolhead_board_combo(preset.supported_toolhead_boards)
        self._populate_probe_types(preset)
        self._apply_addon_support(preset)

        self.macros_group.setEnabled(preset.feature_flags.macros_supported)
        if not preset.feature_flags.macros_supported:
            for checkbox in self.macro_checkboxes.values():
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)

        if not self._applying_project:
            self.dimension_x.setValue(preset.build_volume.x)
            self.dimension_y.setValue(preset.build_volume.y)
            self.dimension_z.setValue(preset.build_volume.z)
            if not preset.feature_flags.probe_optional:
                self.probe_enabled_checkbox.setChecked(True)

        self._sync_probe_controls()
        self._sync_toolhead_controls()
        self._sync_led_controls()
        self._refresh_board_summary()
        self._render_and_validate()

    def _populate_board_combo(self, board_ids: list[str]) -> None:
        current = self.board_combo.currentData()
        self.board_combo.blockSignals(True)
        self.board_combo.clear()
        for board_id in board_ids:
            self.board_combo.addItem(self._format_board_label(board_id), board_id)

        restored = self._set_combo_to_value(self.board_combo, current)
        if not restored and self.board_combo.count() > 0:
            self.board_combo.setCurrentIndex(0)
        self.board_combo.blockSignals(False)

    def _populate_toolhead_board_combo(self, board_ids: list[str]) -> None:
        current = self.toolhead_board_combo.currentData()
        self.toolhead_board_combo.blockSignals(True)
        self.toolhead_board_combo.clear()
        self.toolhead_board_combo.addItem("None", None)
        for board_id in board_ids:
            self.toolhead_board_combo.addItem(self._format_toolhead_board_label(board_id), board_id)

        self._set_combo_to_value(self.toolhead_board_combo, current)
        self.toolhead_board_combo.blockSignals(False)

    def _populate_probe_types(self, preset: Preset) -> None:
        current = self.probe_type_combo.currentText().strip()
        probe_types = list(dict.fromkeys([*preset.recommended_probe_types, *self.DEFAULT_PROBE_TYPES]))
        self.probe_type_combo.blockSignals(True)
        self.probe_type_combo.clear()
        self.probe_type_combo.addItem("")
        for probe_type in probe_types:
            self.probe_type_combo.addItem(probe_type)
        if current:
            self.probe_type_combo.setCurrentText(current)
        self.probe_type_combo.blockSignals(False)

    def _apply_addon_support(self, preset: Preset) -> None:
        supported = set(preset.supported_addons)
        for addon_name, checkbox in self.addon_checkboxes.items():
            is_supported = addon_name in supported
            checkbox.setEnabled(is_supported)
            if not is_supported:
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)

    def _on_macro_checkbox_toggled(self, _macro_name: str, _checked: bool) -> None:
        self._render_and_validate()

    def _on_addon_checkbox_toggled(self, _addon_name: str, _checked: bool) -> None:
        self._render_and_validate()

    def _sync_probe_controls(self) -> None:
        self.probe_type_combo.setEnabled(self.probe_enabled_checkbox.isChecked())

    def _sync_toolhead_controls(self) -> None:
        enabled = self.toolhead_enabled_checkbox.isChecked()
        self.toolhead_board_combo.setEnabled(enabled)
        self.toolhead_canbus_uuid_edit.setEnabled(enabled)

    def _sync_led_controls(self) -> None:
        enabled = self.led_enabled_checkbox.isChecked()
        self.led_pin_edit.setEnabled(enabled)
        self.led_chain_count_spin.setEnabled(enabled)
        self.led_color_order_combo.setEnabled(enabled)
        self.led_initial_red_spin.setEnabled(enabled)
        self.led_initial_green_spin.setEnabled(enabled)
        self.led_initial_blue_spin.setEnabled(enabled)

    def _collect_macro_packs(self) -> list[str]:
        return [
            name
            for name, checkbox in self.macro_checkboxes.items()
            if checkbox.isChecked() and checkbox.isEnabled()
        ]

    def _collect_addons(self) -> list[str]:
        return [
            name
            for name, checkbox in self.addon_checkboxes.items()
            if checkbox.isChecked() and checkbox.isEnabled()
        ]

    def _build_project_from_ui(self) -> ProjectConfig:
        if self.current_preset is None:
            raise ValueError("No preset selected.")

        board_id = self.board_combo.currentData()
        if not isinstance(board_id, str):
            board_id = ""

        probe_enabled = self.probe_enabled_checkbox.isChecked()
        probe_type = self.probe_type_combo.currentText().strip() or None

        toolhead_enabled = self.toolhead_enabled_checkbox.isChecked()
        toolhead_board = self.toolhead_board_combo.currentData() if toolhead_enabled else None
        if toolhead_enabled and not isinstance(toolhead_board, str):
            toolhead_board = None
        toolhead_uuid = self.toolhead_canbus_uuid_edit.text().strip() or None
        if not toolhead_enabled:
            toolhead_uuid = None

        payload = {
            "preset_id": self.current_preset.id,
            "board": board_id,
            "dimensions": {
                "x": self.dimension_x.value(),
                "y": self.dimension_y.value(),
                "z": self.dimension_z.value(),
            },
            "probe": {
                "enabled": probe_enabled,
                "type": probe_type if probe_enabled else None,
            },
            "thermistors": {
                "hotend": self.hotend_thermistor_edit.text().strip() or "EPCOS 100K B57560G104F",
                "bed": self.bed_thermistor_edit.text().strip() or "EPCOS 100K B57560G104F",
            },
            "motion_profile": "safe",
            "macro_packs": self._collect_macro_packs(),
            "addons": self._collect_addons(),
            "toolhead": {
                "enabled": toolhead_enabled,
                "board": toolhead_board,
                "canbus_uuid": toolhead_uuid,
            },
            "leds": {
                "enabled": self.led_enabled_checkbox.isChecked(),
                "pin": self.led_pin_edit.text().strip() or None,
                "chain_count": self.led_chain_count_spin.value(),
                "color_order": self.led_color_order_combo.currentText(),
                "initial_red": float(self.led_initial_red_spin.value()),
                "initial_green": float(self.led_initial_green_spin.value()),
                "initial_blue": float(self.led_initial_blue_spin.value()),
            },
            "advanced_overrides": self._collect_advanced_overrides(),
        }
        return ProjectConfig.model_validate(payload)
    def _collect_advanced_overrides(self) -> dict[str, Any]:
        overrides: dict[str, Any] = {}
        for row in range(self.overrides_table.rowCount()):
            key_item = self.overrides_table.item(row, 0)
            value_item = self.overrides_table.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            if not key:
                continue
            value_text = value_item.text().strip() if value_item else ""
            overrides[key] = self._parse_override_value(value_text)
        return overrides

    @staticmethod
    def _parse_override_value(raw: str) -> Any:
        text = raw.strip()
        lowered = text.lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        if lowered in {"none", "null"}:
            return None
        try:
            if "." not in text and "e" not in lowered:
                return int(text)
            return float(text)
        except ValueError:
            return text

    def _render_and_validate(self) -> None:
        if self.current_preset is None:
            return

        try:
            project = self._build_project_from_ui()
        except (ValidationError, ValueError) as exc:
            report = ValidationReport()
            if isinstance(exc, ValidationError):
                for error in exc.errors():
                    loc = ".".join(str(piece) for piece in error.get("loc", []))
                    report.add(
                        severity="blocking",
                        code="PROJECT_BUILD_FAILED",
                        message=error.get("msg", "Invalid project configuration."),
                        field=loc or None,
                    )
            else:
                report.add(
                    severity="blocking",
                    code="PROJECT_BUILD_FAILED",
                    message=str(exc),
                )
            self.current_project = None
            self.current_pack = None
            self.current_report = report
            self._update_validation_view(report)
            self._update_generated_files_view(None)
            self._update_action_enablement()
            self._refresh_board_summary()
            return

        preset = self.presets_by_id.get(project.preset_id)
        if preset is None:
            report = ValidationReport()
            report.add(
                severity="blocking",
                code="PRESET_NOT_FOUND",
                message=f"Preset '{project.preset_id}' is unavailable.",
                field="preset_id",
            )
            self.current_project = project
            self.current_pack = None
            self.current_report = report
            self._update_validation_view(report)
            self._update_generated_files_view(None)
            self._update_action_enablement()
            self._refresh_board_summary()
            return

        project_report = self.validation_service.validate_project(project, preset)

        render_report = ValidationReport()
        pack: RenderedPack | None = None
        try:
            pack = self.render_service.render(project, preset)
            render_report = self.validation_service.validate_rendered(pack)
        except Exception as exc:  # noqa: BLE001
            render_report.add(
                severity="blocking",
                code="RENDER_FAILED",
                message=str(exc),
            )

        combined = ValidationReport(findings=[*project_report.findings, *render_report.findings])

        self.current_project = project
        self.current_pack = pack
        self.current_report = combined

        self._update_validation_view(combined)
        self._update_generated_files_view(pack)
        self._update_action_enablement()
        self._refresh_board_summary()
        self.statusBar().showMessage("Render + validation complete", 2500)

    def _update_validation_view(self, report: ValidationReport) -> None:
        sorted_findings = sorted(
            report.findings,
            key=lambda finding: 0 if finding.severity == "blocking" else 1,
        )

        self.validation_table.setRowCount(len(sorted_findings))
        for row, finding in enumerate(sorted_findings):
            severity_item = QTableWidgetItem(finding.severity)
            code_item = QTableWidgetItem(finding.code)
            field_item = QTableWidgetItem(finding.field or "")
            message_item = QTableWidgetItem(finding.message)

            color = Qt.GlobalColor.red if finding.severity == "blocking" else Qt.GlobalColor.darkYellow
            severity_item.setForeground(color)

            self.validation_table.setItem(row, 0, severity_item)
            self.validation_table.setItem(row, 1, code_item)
            self.validation_table.setItem(row, 2, field_item)
            self.validation_table.setItem(row, 3, message_item)

        blocking_count = sum(1 for finding in report.findings if finding.severity == "blocking")
        warning_count = sum(1 for finding in report.findings if finding.severity == "warning")
        if blocking_count > 0:
            self.validation_status_label.setText(
                f"Blocking issues: {blocking_count}. Warnings: {warning_count}. "
                "Fix blocking issues before export/deploy."
            )
        elif warning_count > 0:
            self.validation_status_label.setText(f"No blocking issues. Warnings: {warning_count}.")
        else:
            self.validation_status_label.setText("No validation issues detected.")
        self._update_validation_issue_notification(blocking_count, warning_count)
        self._update_live_conflict_alert(report, sorted_findings)

    def _update_validation_issue_notification(self, blocking_count: int, warning_count: int) -> None:
        total = blocking_count + warning_count

        if hasattr(self, "validation_section_toggle"):
            title = "Validation Findings"
            if total > 0:
                title = f"{title} ({total})"
            self.validation_section_toggle.setText(title)

        if not hasattr(self, "files_validation_notice_label"):
            return

        if total == 0:
            self.files_validation_notice_label.clear()
            self.files_validation_notice_label.setVisible(False)
            self.files_validation_notice_label.setStyleSheet("")
            return

        plural = "issue" if total == 1 else "issues"
        if blocking_count > 0:
            message = (
                f"Validation unresolved: {blocking_count} blocking and {warning_count} warning "
                f"{plural}. Expand 'Validation Findings' to review."
            )
            style = (
                "QLabel {"
                " background-color: #7f1d1d;"
                " color: #ffffff;"
                " border: 1px solid #ef4444;"
                " border-radius: 4px;"
                " padding: 6px 8px;"
                " font-weight: 600;"
                "}"
            )
        else:
            message = (
                f"Validation unresolved: {warning_count} warning {plural}. "
                "Expand 'Validation Findings' to review."
            )
            style = (
                "QLabel {"
                " background-color: #78350f;"
                " color: #ffffff;"
                " border: 1px solid #f59e0b;"
                " border-radius: 4px;"
                " padding: 6px 8px;"
                " font-weight: 600;"
                "}"
            )

        self.files_validation_notice_label.setText(message)
        self.files_validation_notice_label.setStyleSheet(style)
        self.files_validation_notice_label.setVisible(True)

    def _update_live_conflict_alert(
        self,
        report: ValidationReport,
        sorted_findings: list[Any] | None = None,
    ) -> None:
        findings = sorted_findings if sorted_findings is not None else list(report.findings)
        blocking_findings = [finding for finding in findings if finding.severity == "blocking"]

        if not blocking_findings:
            if self._last_blocking_alert_snapshot:
                self.statusBar().showMessage("Conflicts resolved", 2500)
            self._last_blocking_alert_snapshot = ()
            self.conflict_alert_label.clear()
            self.conflict_alert_label.setVisible(False)
            self.conflict_alert_label.setStyleSheet("")
            return

        message_parts = [
            f"{finding.code}: {finding.message}"
            for finding in blocking_findings[:2]
        ]
        if len(blocking_findings) > 2:
            message_parts.append(f"+{len(blocking_findings) - 2} more")

        summary = (
            f"Blocking conflicts ({len(blocking_findings)}). "
            + " | ".join(message_parts)
        )
        self.conflict_alert_label.setText(summary)
        self.conflict_alert_label.setStyleSheet(
            "QLabel {"
            " background-color: #7f1d1d;"
            " color: #ffffff;"
            " border: 1px solid #ef4444;"
            " border-radius: 4px;"
            " padding: 6px 8px;"
            " font-weight: 600;"
            "}"
        )
        self.conflict_alert_label.setVisible(True)

        snapshot = tuple(
            f"{finding.code}|{finding.field or ''}|{finding.message}"
            for finding in blocking_findings
        )
        if snapshot != self._last_blocking_alert_snapshot:
            self.statusBar().showMessage(f"Blocking conflicts: {len(blocking_findings)}", 3000)
        self._last_blocking_alert_snapshot = snapshot

    def _update_generated_files_view(self, pack: RenderedPack | None) -> None:
        if self._showing_external_file:
            return

        self.generated_file_list.blockSignals(True)
        self.generated_file_list.clear()
        if pack is not None:
            for name in pack.files.keys():
                self.generated_file_list.addItem(name)
        self.generated_file_list.blockSignals(False)

        if pack is not None and self.generated_file_list.count() > 0:
            self.generated_file_list.setCurrentRow(0)
            self._show_selected_generated_file()
        else:
            self._set_files_tab_content(
                content="",
                label="No generated files.",
                source="generated",
                generated_name=None,
            )

    def _update_action_enablement(self) -> None:
        can_output = (
            self.current_project is not None
            and self.current_pack is not None
            and not self.current_report.has_blocking
        )
        self.export_folder_btn.setEnabled(can_output)
        self.export_zip_btn.setEnabled(can_output)
        self.deploy_btn.setEnabled(can_output)

    def _on_generated_file_selected(self) -> None:
        if self._showing_external_file:
            return
        self._show_selected_generated_file()

    def _show_selected_generated_file(self) -> None:
        self._showing_external_file = False
        if self.current_pack is None:
            self._set_files_tab_content(
                content="",
                label="No generated files.",
                source="generated",
                generated_name=None,
            )
            return

        item = self.generated_file_list.currentItem()
        if item is None and self.generated_file_list.count() > 0:
            self.generated_file_list.setCurrentRow(0)
            item = self.generated_file_list.currentItem()
        if item is None:
            self._set_files_tab_content(
                content="",
                label="No generated files.",
                source="generated",
                generated_name=None,
            )
            return

        file_name = item.text()
        contents = self.current_pack.files.get(file_name, "")
        self._set_files_tab_content(
            content=contents,
            label=f"Generated: {file_name}",
            source="generated",
            generated_name=file_name,
        )

    def _open_local_cfg_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Klipper Config",
            str(Path.home()),
            "Klipper config (*.cfg *.txt *.log *.md);;All files (*.*)",
        )
        if not file_path:
            return

        try:
            contents = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._show_error("File Open Failed", str(exc))
            return

        self._showing_external_file = True
        self._set_files_tab_content(
            content=contents,
            label=file_path,
            source="local",
            generated_name=None,
        )
        self.tabs.setCurrentWidget(self.files_tab)

    def _set_files_tab_content(
        self,
        content: str,
        label: str,
        source: str,
        generated_name: str | None,
    ) -> None:
        self.files_current_content = content
        self.files_current_label = label
        self.files_current_source = source
        self.files_current_generated_name = generated_name
        self.file_preview.setPlainText(content)
        self.preview_path_label.setText(label)
        self._rebuild_cfg_form()

    @staticmethod
    def _is_cfg_label(label: str, generated_name: str | None) -> bool:
        if generated_name:
            return generated_name.lower().endswith(".cfg")
        lower = label.lower()
        return lower.endswith(".cfg") or ".cfg:" in lower or "/.cfg" in lower

    def _clear_cfg_form(self) -> None:
        self.cfg_form_editors.clear()
        layout = self.form_container_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        layout.addStretch(1)

    def _rebuild_cfg_form(self) -> None:
        self._clear_cfg_form()
        self.apply_form_btn.setEnabled(False)

        if not self.files_current_content.strip():
            self.form_summary_label.setText("No file loaded.")
            return
        if not self._is_cfg_label(self.files_current_label, self.files_current_generated_name):
            self.form_summary_label.setText("Forms are available for .cfg files only.")
            return

        lines = self.files_current_content.splitlines()
        parsed = self._parse_cfg_fields(lines)
        if not parsed:
            self.form_summary_label.setText(
                "No simple editable key/value fields found. Multi-line blocks remain in raw view."
            )
            return

        sections: dict[str, list[dict[str, Any]]] = {}
        for field in parsed:
            sections.setdefault(field["section"], []).append(field)

        for section_name, entries in sections.items():
            section_group = QGroupBox(f"[{section_name}]")
            section_layout = QFormLayout(section_group)
            for entry in entries:
                editor = QLineEdit(entry["value"], section_group)
                editor.setProperty("line_index", entry["line_index"])
                editor.setProperty("key", entry["key"])
                editor.setProperty("section", entry["section"])
                section_layout.addRow(entry["key"], editor)
                self.cfg_form_editors.append(
                    {
                        "section": entry["section"],
                        "key": entry["key"],
                        "line_index": entry["line_index"],
                        "editor": editor,
                    }
                )
            self.form_container_layout.insertWidget(self.form_container_layout.count() - 1, section_group)

        self.form_summary_label.setText(
            f"Editable fields: {len(self.cfg_form_editors)} across {len(sections)} section(s)."
        )
        self.apply_form_btn.setEnabled(True)

    @staticmethod
    def _parse_cfg_fields(lines: list[str]) -> list[dict[str, Any]]:
        section = "global"
        parsed: list[dict[str, Any]] = []
        section_pattern = re.compile(r"^\s*\[([^\]]+)\]\s*$")
        key_pattern = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*:\s*(.*)$")

        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#") or stripped.startswith(";"):
                continue

            section_match = section_pattern.match(line)
            if section_match:
                section = section_match.group(1).strip() or "global"
                continue

            if line[:1].isspace():
                continue

            key_match = key_pattern.match(line)
            if not key_match:
                continue

            key = key_match.group(1).strip()
            value = key_match.group(2).strip()

            has_multiline_value = False
            probe = index + 1
            while probe < len(lines):
                nxt = lines[probe]
                nxt_stripped = nxt.strip()
                if not nxt_stripped:
                    probe += 1
                    continue
                if nxt_stripped.startswith("#") or nxt_stripped.startswith(";"):
                    probe += 1
                    continue
                if nxt[:1].isspace():
                    has_multiline_value = True
                break
            if has_multiline_value:
                continue

            parsed.append(
                {
                    "section": section,
                    "key": key,
                    "value": value,
                    "line_index": index,
                }
            )
        return parsed

    def _apply_cfg_form_changes(self) -> None:
        if not self.cfg_form_editors:
            self._show_error("Files", "No editable form fields are available for the current file.")
            return

        original = self.files_current_content
        lines = original.splitlines()
        trailing_newline = original.endswith("\n")

        for entry in self.cfg_form_editors:
            line_index = int(entry["line_index"])
            key = str(entry["key"])
            editor = entry["editor"]
            if line_index < 0 or line_index >= len(lines):
                continue
            assert isinstance(editor, QLineEdit)
            value = editor.text().strip()
            lines[line_index] = f"{key}: {value}" if value else f"{key}:"

        updated = "\n".join(lines)
        if trailing_newline:
            updated += "\n"

        self.files_current_content = updated
        self.file_preview.setPlainText(updated)

        if (
            self.files_current_source == "generated"
            and self.current_pack is not None
            and self.files_current_generated_name
        ):
            self.current_pack.files[self.files_current_generated_name] = updated

        self.statusBar().showMessage("Applied form changes to current file view", 2500)

    def _ensure_export_ready(self) -> bool:
        if self.current_pack is None:
            self._render_and_validate()
        if self.current_pack is None:
            self._show_error("Export Blocked", "No generated pack is available.")
            return False
        if self.current_report.has_blocking:
            self._show_error(
                "Export Blocked",
                "Resolve blocking validation issues before exporting or deploying.",
            )
            self.tabs.setCurrentWidget(self.files_tab)
            return False
        return True

    def _export_folder(self) -> None:
        if not self._ensure_export_ready():
            return

        directory = QFileDialog.getExistingDirectory(self, "Select Export Folder", str(Path.home()))
        if not directory:
            return

        assert self.current_pack is not None
        try:
            self.export_service.export_folder(self.current_pack, directory)
        except OSError as exc:
            self._show_error("Export Failed", str(exc))
            return

        self.export_status_label.setText(f"Folder export complete: {directory}")
        self.statusBar().showMessage("Exported folder", 2500)

    def _export_zip(self) -> None:
        if not self._ensure_export_ready():
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export ZIP",
            str(Path.home() / "klippconfig-pack.zip"),
            "ZIP files (*.zip)",
        )
        if not file_path:
            return

        zip_path = Path(file_path)
        if zip_path.suffix.lower() != ".zip":
            zip_path = zip_path.with_suffix(".zip")

        assert self.current_pack is not None
        try:
            self.export_service.export_zip(self.current_pack, str(zip_path))
        except OSError as exc:
            self._show_error("Export Failed", str(exc))
            return

        self.export_status_label.setText(f"ZIP export complete: {zip_path}")
        self.statusBar().showMessage("Exported zip", 2500)
    def _save_project_to_file(self) -> None:
        try:
            project = self._build_project_from_ui()
        except (ValidationError, ValueError) as exc:
            self._show_error("Save Failed", str(exc))
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project",
            str(Path.home() / "klippconfig-project.json"),
            "KlippConfig project (*.json)",
        )
        if not file_path:
            return

        try:
            self.project_store.save(file_path, project)
        except OSError as exc:
            self._show_error("Save Failed", str(exc))
            return

        self.statusBar().showMessage(f"Saved project: {file_path}", 2500)

    def _load_project_from_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Project",
            str(Path.home()),
            "KlippConfig project (*.json)",
        )
        if not file_path:
            return

        try:
            project = self.project_store.load(file_path)
        except Exception as exc:  # noqa: BLE001
            self._show_error("Load Failed", str(exc))
            return

        self._apply_project_to_ui(project)
        self._render_and_validate()
        self.statusBar().showMessage(f"Loaded project: {file_path}", 2500)

    def _new_project(self) -> None:
        if self.preset_combo.count() == 0:
            return
        self._applying_project = False
        self.preset_combo.setCurrentIndex(0)
        self._on_preset_changed(0)
        self._clear_overrides(skip_confirm=True)
        self.led_enabled_checkbox.setChecked(False)
        self.led_pin_edit.setText("PA8")
        self.led_chain_count_spin.setValue(1)
        self.led_color_order_combo.setCurrentText("GRB")
        self.led_initial_red_spin.setValue(0.0)
        self.led_initial_green_spin.setValue(0.0)
        self.led_initial_blue_spin.setValue(0.0)
        self._sync_led_controls()
        self.statusBar().showMessage("New project ready", 2500)

    def _apply_project_to_ui(self, project: ProjectConfig) -> None:
        self._applying_project = True
        try:
            preset_index = self.preset_combo.findData(project.preset_id)
            if preset_index < 0:
                raise ValueError(f"Preset '{project.preset_id}' is not available in this build.")
            self.preset_combo.setCurrentIndex(preset_index)
            self._on_preset_changed(preset_index)

            board_index = self.board_combo.findData(project.board)
            if board_index >= 0:
                self.board_combo.setCurrentIndex(board_index)

            self.dimension_x.setValue(project.dimensions.x)
            self.dimension_y.setValue(project.dimensions.y)
            self.dimension_z.setValue(project.dimensions.z)

            self.probe_enabled_checkbox.setChecked(project.probe.enabled)
            self.probe_type_combo.setCurrentText(project.probe.type or "")

            self.hotend_thermistor_edit.setText(project.thermistors.hotend)
            self.bed_thermistor_edit.setText(project.thermistors.bed)

            self.toolhead_enabled_checkbox.setChecked(project.toolhead.enabled)
            toolhead_index = self.toolhead_board_combo.findData(project.toolhead.board)
            if toolhead_index >= 0:
                self.toolhead_board_combo.setCurrentIndex(toolhead_index)
            self.toolhead_canbus_uuid_edit.setText(project.toolhead.canbus_uuid or "")

            self.led_enabled_checkbox.setChecked(project.leds.enabled)
            self.led_pin_edit.setText(project.leds.pin or "")
            self.led_chain_count_spin.setValue(project.leds.chain_count)
            self.led_color_order_combo.setCurrentText(project.leds.color_order)
            self.led_initial_red_spin.setValue(project.leds.initial_red)
            self.led_initial_green_spin.setValue(project.leds.initial_green)
            self.led_initial_blue_spin.setValue(project.leds.initial_blue)

            for name, checkbox in self.macro_checkboxes.items():
                checkbox.setChecked(name in project.macro_packs)
            for name, checkbox in self.addon_checkboxes.items():
                checkbox.setChecked(name in project.addons and checkbox.isEnabled())

            self._replace_overrides(project.advanced_overrides)
            self._sync_probe_controls()
            self._sync_toolhead_controls()
            self._sync_led_controls()
        finally:
            self._applying_project = False

    def _replace_overrides(self, overrides: dict[str, Any]) -> None:
        self.overrides_table.blockSignals(True)
        self.overrides_table.setRowCount(0)
        for key, value in sorted(overrides.items()):
            self._add_override_row(key, str(value), trigger_render=False)
        self.overrides_table.blockSignals(False)

    def _add_override_row(self, key: str = "", value: str = "", trigger_render: bool = True) -> None:
        self.overrides_table.blockSignals(True)
        row = self.overrides_table.rowCount()
        self.overrides_table.insertRow(row)
        self.overrides_table.setItem(row, 0, QTableWidgetItem(key))
        self.overrides_table.setItem(row, 1, QTableWidgetItem(value))
        self.overrides_table.blockSignals(False)
        if trigger_render:
            self._render_and_validate()

    def _remove_selected_override_rows(self) -> None:
        rows = sorted({index.row() for index in self.overrides_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        self.overrides_table.blockSignals(True)
        for row in rows:
            self.overrides_table.removeRow(row)
        self.overrides_table.blockSignals(False)
        self._render_and_validate()

    def _clear_overrides(self, skip_confirm: bool = False) -> None:
        if not skip_confirm and self.overrides_table.rowCount() > 0:
            answer = QMessageBox.question(self, "Clear Overrides", "Remove all advanced overrides?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.overrides_table.blockSignals(True)
        self.overrides_table.setRowCount(0)
        self.overrides_table.blockSignals(False)
        self._render_and_validate()

    def _refresh_board_summary(self) -> None:
        board_id = self.board_combo.currentData()
        toolhead_id = self.toolhead_board_combo.currentData()
        lines: list[str] = []

        if isinstance(board_id, str):
            main_profile = get_board_profile(board_id)
            lines.append(f"Mainboard: {self._format_board_label(board_id)}")
            if main_profile:
                lines.append(f"MCU: {main_profile.mcu}")
                lines.append(f"Serial hint: {main_profile.serial_hint}")
                if main_profile.layout:
                    lines.append("Connectors:")
                    for section, connectors in main_profile.layout.items():
                        lines.append(f"  - {section}: {', '.join(connectors)}")

        if self.toolhead_enabled_checkbox.isChecked() and isinstance(toolhead_id, str):
            tool_profile = get_toolhead_board_profile(toolhead_id)
            lines.append("")
            lines.append(f"Toolhead: {self._format_toolhead_board_label(toolhead_id)}")
            if tool_profile:
                lines.append(f"Toolhead MCU: {tool_profile.mcu}")
                if tool_profile.layout:
                    lines.append("Toolhead connectors:")
                    for section, connectors in tool_profile.layout.items():
                        lines.append(f"  - {section}: {', '.join(connectors)}")

        lines.append("")
        lines.append(f"LEDs enabled: {'yes' if self.led_enabled_checkbox.isChecked() else 'no'}")
        if self.led_enabled_checkbox.isChecked():
            lines.append(f"LED pin: {self.led_pin_edit.text().strip() or 'unset'}")
            lines.append(f"LED chain count: {self.led_chain_count_spin.value()}")
            lines.append(f"LED color order: {self.led_color_order_combo.currentText()}")

        if not lines:
            lines.append("No board selected.")
        self.board_summary.setPlainText("\n".join(lines))

    def _scan_for_printers(self) -> None:
        cidr = self.scan_cidr_edit.text().strip()
        timeout = float(self.scan_timeout_spin.value())
        max_hosts = int(self.scan_max_hosts_spin.value())

        self.scan_network_btn.setEnabled(False)
        self.statusBar().showMessage("Scanning network for printers...", 0)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            results = self.discovery_service.scan(
                cidr,
                timeout=timeout,
                max_hosts=max_hosts,
            )
        except PrinterDiscoveryError as exc:
            self._show_error("Discovery Failed", str(exc))
            self._append_ssh_log(f"Discovery failed: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.scan_network_btn.setEnabled(True)

        self._populate_discovery_results(results)
        if results:
            self._append_ssh_log(
                f"Discovery complete: {len(results)} likely printer host(s) found in {cidr}."
            )
            self.statusBar().showMessage(f"Found {len(results)} host(s)", 3000)
        else:
            self._append_ssh_log(f"Discovery complete: no printers found in {cidr}.")
            self.statusBar().showMessage("No printers found", 3000)

    def _populate_discovery_results(self, results: list[DiscoveredPrinter]) -> None:
        self.discovery_results_table.setRowCount(len(results))
        for row, item in enumerate(results):
            host_item = QTableWidgetItem(item.host)
            host_item.setData(Qt.ItemDataRole.UserRole, item.host)
            moonraker_item = QTableWidgetItem("yes" if item.moonraker else "no")
            ssh_item = QTableWidgetItem("yes" if item.ssh else "no")

            details: list[str] = []
            if item.moonraker_status:
                details.append(f"Moonraker {item.moonraker_status}")
            if item.ssh_banner:
                details.append(item.ssh_banner)
            detail_item = QTableWidgetItem(" | ".join(details))

            self.discovery_results_table.setItem(row, 0, host_item)
            self.discovery_results_table.setItem(row, 1, moonraker_item)
            self.discovery_results_table.setItem(row, 2, ssh_item)
            self.discovery_results_table.setItem(row, 3, detail_item)

        if results:
            self.discovery_results_table.setCurrentCell(0, 0)

    def _use_selected_discovery_host(self) -> None:
        selected = self.discovery_results_table.selectedItems()
        if not selected:
            self._show_error("Discovery", "Select a discovered host first.")
            return

        row = selected[0].row()
        host_item = self.discovery_results_table.item(row, 0)
        if host_item is None:
            self._show_error("Discovery", "Selected row has no host value.")
            return

        host = host_item.data(Qt.ItemDataRole.UserRole) or host_item.text().strip()
        if not host:
            self._show_error("Discovery", "Selected row has an invalid host.")
            return
        self.ssh_host_edit.setText(str(host))
        self.manage_host_edit.setText(str(host))
        self._append_ssh_log(f"Using discovered host: {host}")
        self._append_manage_log(f"Using discovered host: {host}")
        self.statusBar().showMessage(f"Host set to {host}", 2500)

    def _sync_manage_remote_dir_from_ssh(self, value: str) -> None:
        if not self.manage_remote_dir_edit.text().strip():
            self.manage_remote_dir_edit.setText(value.strip())

    def _use_ssh_host_for_manage(self) -> None:
        host = self.ssh_host_edit.text().strip()
        if not host:
            self._show_error("Manage Printer", "SSH host is empty.")
            return
        self.manage_host_edit.setText(host)
        self._append_manage_log(f"Using SSH host: {host}")

    def _resolve_manage_host(self) -> str:
        return self.manage_host_edit.text().strip() or self.ssh_host_edit.text().strip()

    @staticmethod
    def _normalize_control_url(raw_value: str) -> str:
        value = raw_value.strip()
        if not value:
            return ""
        if "://" not in value:
            value = f"http://{value}"
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            return ""
        return value

    def _resolve_manage_control_url(self) -> str:
        manual_url = self.manage_control_url_edit.text().strip()
        source = manual_url or self._resolve_manage_host()
        normalized = self._normalize_control_url(source)
        if normalized:
            return normalized
        if manual_url:
            self._show_error(
                "Manage Printer",
                "Control URL is invalid. Example: http://192.168.1.20/ or printer.local.",
            )
        else:
            self._show_error("Manage Printer", "Set a host in SSH or Manage Printer tab.")
        return ""

    def _create_control_window(self, url: str) -> QMainWindow:
        return PrinterControlWindow(url, self)

    def _on_manage_control_window_closed(self, window: QMainWindow) -> None:
        self.manage_control_windows = [
            open_window for open_window in self.manage_control_windows if open_window is not window
        ]

    def _manage_open_control_window(self) -> None:
        control_url = self._resolve_manage_control_url()
        if not control_url:
            return

        try:
            control_window = self._create_control_window(control_url)
        except RuntimeError as exc:
            opened = QDesktopServices.openUrl(QUrl(control_url))
            if opened:
                self._append_manage_log(f"{exc} Opened in external browser: {control_url}")
                self.statusBar().showMessage("Embedded view unavailable; opened browser", 3500)
                return
            self._show_error("Manage Printer", str(exc))
            return

        self.manage_control_windows.append(control_window)
        control_window.destroyed.connect(
            lambda *_args, control_ref=control_window: self._on_manage_control_window_closed(
                control_ref
            )
        )
        control_window.show()
        control_window.raise_()
        control_window.activateWindow()
        self._append_manage_log(f"Opened control window: {control_url}")
        self.statusBar().showMessage(f"Control window opened: {control_url}", 3000)

    def _collect_manage_params(self) -> dict[str, Any] | None:
        host = self._resolve_manage_host()
        if not host:
            self._show_error("Manage Printer", "Set a host in SSH or Manage Printer tab.")
            return None
        return self._collect_ssh_params(host_override=host)

    def _append_manage_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.manage_log.appendPlainText(f"[{stamp}] {message}")

    def _manage_resolve_root_directory(self) -> str:
        return self.manage_remote_dir_edit.text().strip() or self.ssh_remote_dir_edit.text().strip()

    @staticmethod
    def _manage_parent_directory(path: str) -> str:
        normalized = path.rstrip("/") or "/"
        parent = posixpath.dirname(normalized) or "/"
        return parent

    def _manage_browse_up_directory(self) -> None:
        current = self.manage_current_directory or self._manage_resolve_root_directory()
        if not current:
            self._show_error("Manage Printer", "Remote cfg dir is empty.")
            return
        parent = self._manage_parent_directory(current)
        self._manage_refresh_files(target_dir=parent)

    def _manage_refresh_files(self, target_dir: str | None = None) -> None:
        service = self._get_ssh_service()
        if service is None:
            return
        params = self._collect_manage_params()
        if params is None:
            return

        remote_dir = (target_dir or self._manage_resolve_root_directory()).strip()
        if not remote_dir:
            self._show_error("Manage Printer", "Remote cfg dir is empty.")
            return

        self.manage_refresh_files_btn.setEnabled(False)
        self.manage_up_dir_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            listing = service.list_directory(
                remote_dir=remote_dir,
                **params,
            )
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._show_error("Manage Printer", str(exc))
            self._append_manage_log(f"File refresh failed: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.manage_refresh_files_btn.setEnabled(True)
            self.manage_up_dir_btn.setEnabled(True)

        self.manage_file_list.clear()
        current_dir = str(listing.get("directory") or remote_dir).strip()
        entries = list(listing.get("entries") or [])
        for entry in entries:
            entry_type = str(entry.get("type") or "file")
            name = str(entry.get("name") or "")
            remote_path = str(entry.get("path") or "")
            if not name or not remote_path:
                continue
            display = f"{name}/" if entry_type == "dir" else name
            list_item = QListWidgetItem(display)
            list_item.setData(Qt.ItemDataRole.UserRole, remote_path)
            list_item.setData(Qt.ItemDataRole.UserRole + 1, entry_type)
            self.manage_file_list.addItem(list_item)

        self.manage_current_directory = current_dir
        self.manage_current_dir_label.setText(f"Browsing: {current_dir}")
        self.manage_remote_dir_edit.setText(current_dir)
        self.manage_current_remote_file = None
        self.manage_current_file_label.setText("No file loaded.")
        self.manage_file_editor.clear()
        self._append_manage_log(
            f"Loaded {len(entries)} entries from {current_dir}."
        )
        self._set_device_connection_health(True, f"Host {params['host']} reachable.")
        self.statusBar().showMessage(f"Loaded {len(entries)} entries", 2500)

    def _manage_file_selection_changed(self) -> None:
        selected = self.manage_file_list.selectedItems()
        if not selected:
            return
        item = selected[0]
        remote_path = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
        entry_type = str(item.data(Qt.ItemDataRole.UserRole + 1) or "file")
        if entry_type == "dir":
            self.manage_current_file_label.setText(f"Selected folder: {remote_path}")
        else:
            self.manage_current_file_label.setText(f"Selected file: {remote_path}")

    def _manage_open_selected_file(self) -> None:
        service = self._get_ssh_service()
        if service is None:
            return
        params = self._collect_manage_params()
        if params is None:
            return

        selected = self.manage_file_list.selectedItems()
        if not selected:
            self._show_error("Manage Printer", "Select a remote file or folder first.")
            return
        remote_path = str(selected[0].data(Qt.ItemDataRole.UserRole) or selected[0].text()).strip()
        entry_type = str(selected[0].data(Qt.ItemDataRole.UserRole + 1) or "file")
        if not remote_path:
            self._show_error("Manage Printer", "Selected item has an invalid file path.")
            return
        if entry_type == "dir":
            self._manage_refresh_files(target_dir=remote_path)
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            content = service.fetch_file(remote_path=remote_path, **params)
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._show_error("Manage Printer", str(exc))
            self._append_manage_log(f"Open failed: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.manage_file_editor.setPlainText(content)
        self.manage_current_remote_file = remote_path
        self.manage_current_file_label.setText(f"Editing: {remote_path}")
        self._append_manage_log(f"Opened {remote_path}.")
        self._set_device_connection_health(True, f"Opened {remote_path}.")
        self.statusBar().showMessage(f"Opened {remote_path}", 2500)

    def _manage_save_current_file(self) -> None:
        service = self._get_ssh_service()
        if service is None:
            return
        params = self._collect_manage_params()
        if params is None:
            return

        remote_path = self.manage_current_remote_file
        if not remote_path:
            selected = self.manage_file_list.selectedItems()
            if selected:
                selected_type = str(selected[0].data(Qt.ItemDataRole.UserRole + 1) or "file")
                if selected_type != "file":
                    self._show_error("Manage Printer", "Select and open a file before saving.")
                    return
                remote_path = str(selected[0].data(Qt.ItemDataRole.UserRole) or selected[0].text()).strip()
        if not remote_path:
            self._show_error("Manage Printer", "No remote file is loaded for saving.")
            return

        content = self.manage_file_editor.toPlainText()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            saved_path = service.write_file(remote_path=remote_path, content=content, **params)
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._show_error("Manage Printer", str(exc))
            self._append_manage_log(f"Save failed: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.manage_current_remote_file = saved_path
        self.manage_current_file_label.setText(f"Editing: {saved_path}")
        self._append_manage_log(f"Saved {saved_path}.")
        self._set_device_connection_health(True, f"Saved {saved_path}.")
        self.statusBar().showMessage("Remote file saved", 2500)

    def _manage_create_backup(self) -> None:
        service = self._get_ssh_service()
        if service is None:
            return
        params = self._collect_manage_params()
        if params is None:
            return

        remote_dir = self.manage_remote_dir_edit.text().strip() or self.ssh_remote_dir_edit.text().strip()
        if not remote_dir:
            self._show_error("Manage Printer", "Remote cfg dir is empty.")
            return
        backup_root = self.manage_backup_root_edit.text().strip() or "~/klippconfig_backups"

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            backup_path = service.create_backup(
                remote_dir=remote_dir,
                backup_root=backup_root,
                **params,
            )
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._show_error("Manage Printer", str(exc))
            self._append_manage_log(f"Backup failed: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._append_manage_log(f"Backup created: {backup_path}")
        self._set_device_connection_health(True, f"Backup created: {backup_path}.")
        self.statusBar().showMessage(f"Backup created: {backup_path}", 3000)
        self._manage_refresh_backups()

    def _manage_refresh_backups(self) -> None:
        service = self._get_ssh_service()
        if service is None:
            return
        params = self._collect_manage_params()
        if params is None:
            return

        backup_root = self.manage_backup_root_edit.text().strip() or "~/klippconfig_backups"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            backups = service.list_backups(backup_root=backup_root, **params)
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._show_error("Manage Printer", str(exc))
            self._append_manage_log(f"Backup list failed: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.manage_backup_combo.clear()
        self.manage_backup_combo.addItems(backups)
        self._append_manage_log(f"Loaded {len(backups)} backup(s).")
        self._set_device_connection_health(True, f"Backups listed from {backup_root}.")
        self.statusBar().showMessage(f"{len(backups)} backup(s) found", 2500)

    def _manage_restore_selected_backup(self) -> None:
        service = self._get_ssh_service()
        if service is None:
            return
        params = self._collect_manage_params()
        if params is None:
            return

        backup_path = self.manage_backup_combo.currentText().strip()
        if not backup_path:
            self._show_error("Manage Printer", "No backup selected.")
            return

        remote_dir = self.manage_remote_dir_edit.text().strip() or self.ssh_remote_dir_edit.text().strip()
        if not remote_dir:
            self._show_error("Manage Printer", "Remote cfg dir is empty.")
            return

        answer = QMessageBox.question(
            self,
            "Restore Backup",
            (
                f"Restore backup '{backup_path}' to '{remote_dir}'?\n\n"
                "This will overwrite files in the target config directory."
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            service.restore_backup(
                remote_dir=remote_dir,
                backup_path=backup_path,
                clear_before_restore=self.manage_clear_before_restore_checkbox.isChecked(),
                **params,
            )
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._show_error("Manage Printer", str(exc))
            self._append_manage_log(f"Restore failed: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._append_manage_log(f"Restored backup: {backup_path}")
        self._set_device_connection_health(True, f"Restored backup: {backup_path}.")
        self.statusBar().showMessage("Backup restore complete", 3000)
        self._manage_refresh_files()

    def _desktop_backup_download_root(self) -> Path:
        return Path.home() / "Desktop" / "KlippConfig Backups"

    def _build_backup_download_target(self, backup_path: str) -> Path:
        backup_name = Path(backup_path.rstrip("/")).name.strip()
        if not backup_name:
            backup_name = f"backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        root = self._desktop_backup_download_root()
        target = root / backup_name
        if target.exists():
            suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = root / f"{backup_name}-{suffix}"
        return target

    def _manage_download_selected_backup(self) -> None:
        service = self._get_ssh_service()
        if service is None:
            return
        params = self._collect_manage_params()
        if params is None:
            return

        backup_path = self.manage_backup_combo.currentText().strip()
        if not backup_path:
            self._show_error("Manage Printer", "No backup selected.")
            return

        local_target = self._build_backup_download_target(backup_path)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            downloaded_path = service.download_backup(
                backup_path=backup_path,
                local_destination=str(local_target),
                **params,
            )
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._show_error("Manage Printer", str(exc))
            self._append_manage_log(f"Backup download failed: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._append_manage_log(f"Backup downloaded to {downloaded_path}.")
        self._set_device_connection_health(True, f"Downloaded backup to {downloaded_path}.")
        self.statusBar().showMessage(f"Backup downloaded to {downloaded_path}", 4000)

    def _browse_ssh_key(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SSH Private Key",
            str(Path.home()),
            "Key files (*);;All files (*.*)",
        )
        if file_path:
            self.ssh_key_path_edit.setText(file_path)

    def _get_ssh_service(self) -> SSHDeployService | None:
        if self.ssh_service is not None:
            return self.ssh_service
        try:
            self.ssh_service = SSHDeployService()
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._show_error("SSH Unavailable", str(exc))
            return None
        return self.ssh_service

    def _collect_ssh_params(self, host_override: str | None = None) -> dict[str, Any] | None:
        host = host_override.strip() if host_override else self.ssh_host_edit.text().strip()
        username = self.ssh_username_edit.text().strip()
        if not host or not username:
            self._show_error("SSH Input Error", "Host and username are required.")
            return None

        key_path = self.ssh_key_path_edit.text().strip() or None
        password = self.ssh_password_edit.text() or None
        if key_path and not Path(key_path).exists():
            self._show_error("SSH Input Error", f"SSH key does not exist: {key_path}")
            return None

        return {
            "host": host,
            "port": self.ssh_port_spin.value(),
            "username": username,
            "password": password,
            "key_path": key_path,
        }

    def _append_ssh_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.ssh_log.appendPlainText(f"[{stamp}] {message}")

    def _test_ssh_connection(self) -> None:
        service = self._get_ssh_service()
        if service is None:
            return

        params = self._collect_ssh_params()
        if params is None:
            return

        self._append_ssh_log(
            f"Testing connection to {params['username']}@{params['host']}:{params['port']}"
        )
        try:
            ok, output = service.test_connection(**params)
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._append_ssh_log(str(exc))
            self._show_error("SSH Test Failed", str(exc))
            return

        if ok:
            self._set_device_connection_health(True, str(output))
            self._append_ssh_log(f"Connection successful: {output}")
            self.statusBar().showMessage("SSH connection successful", 2500)
            return
        self._set_device_connection_health(False, str(output))
        self._append_ssh_log(f"Connection failed: {output}")

    def _fetch_remote_cfg_file(self) -> None:
        service = self._get_ssh_service()
        if service is None:
            return

        params = self._collect_ssh_params()
        if params is None:
            return

        remote_path = self.ssh_remote_fetch_path_edit.text().strip()
        if not remote_path:
            self._show_error("SSH Input Error", "Remote file path is required.")
            return

        self._append_ssh_log(f"Fetching remote file: {remote_path}")
        try:
            contents = service.fetch_file(remote_path=remote_path, **params)
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._append_ssh_log(str(exc))
            self._show_error("Remote Fetch Failed", str(exc))
            return

        self._showing_external_file = True
        self._set_files_tab_content(
            content=contents,
            label=f"Remote: {remote_path}",
            source="remote",
            generated_name=None,
        )
        self.tabs.setCurrentWidget(self.files_tab)
        self._append_ssh_log("Remote file opened in Files tab.")
        self._set_device_connection_health(True, f"Opened remote file {remote_path}.")

    def _deploy_generated_pack(self) -> None:
        if not self._ensure_export_ready():
            return

        service = self._get_ssh_service()
        if service is None:
            return

        params = self._collect_ssh_params()
        if params is None:
            return

        remote_dir = self.ssh_remote_dir_edit.text().strip()
        if not remote_dir:
            self._show_error("SSH Input Error", "Remote config directory is required.")
            return

        assert self.current_pack is not None
        self._append_ssh_log(
            f"Deploying {len(self.current_pack.files)} files to {params['host']}:{remote_dir}"
        )
        try:
            result = service.deploy_pack(
                pack=self.current_pack,
                remote_dir=remote_dir,
                backup_before_upload=self.ssh_backup_checkbox.isChecked(),
                restart_klipper=self.ssh_restart_checkbox.isChecked(),
                klipper_restart_command=self.ssh_restart_cmd_edit.text().strip()
                or "sudo systemctl restart klipper",
                **params,
            )
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._append_ssh_log(str(exc))
            self._show_error("Deploy Failed", str(exc))
            return

        uploaded = result.get("uploaded", [])
        backup_path = result.get("backup_path")
        restart_output = result.get("restart_output")

        if backup_path:
            self._append_ssh_log(f"Backup created: {backup_path}")
        self._append_ssh_log(f"Uploaded {len(uploaded)} files.")
        if restart_output:
            self._append_ssh_log(f"Restart output: {restart_output}")
        self._set_device_connection_health(True, f"Deployed to {params['host']}.")
        self.statusBar().showMessage("Deploy complete", 2500)

    @staticmethod
    def _set_combo_to_value(combo: QComboBox, value: Any) -> bool:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return True
        return False

    @staticmethod
    def _format_board_label(board_id: str) -> str:
        profile = get_board_profile(board_id)
        if profile is None:
            return board_id
        return f"{profile.label} ({board_id})"

    @staticmethod
    def _format_toolhead_board_label(board_id: str) -> str:
        profile = get_toolhead_board_profile(board_id)
        if profile is None:
            return board_id
        return f"{profile.label} ({board_id})"

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)


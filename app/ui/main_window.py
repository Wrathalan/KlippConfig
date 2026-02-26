
from __future__ import annotations

from datetime import datetime
import json
from queue import Empty, SimpleQueue
from pathlib import Path
import posixpath
import re
import threading
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError
from PySide6.QtCore import QSettings, QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.domain.models import (
    ImportSuggestion,
    ImportedMachineProfile,
    Preset,
    ProjectConfig,
    RenderedPack,
    ValidationReport,
)
from app.services.board_registry import (
    get_board_profile,
    get_toolhead_board_profile,
    list_main_boards,
    list_toolhead_boards,
    refresh_bundle_catalog,
    toolhead_board_transport,
)
from app.services.action_log import ActionLogService
from app.services.exporter import ExportService
from app.services.existing_machine_import import (
    ExistingMachineImportError,
    ExistingMachineImportService,
)
from app.services.firmware_tools import FirmwareToolsService
from app.services.paths import bundles_dir as default_bundles_dir
from app.services.paths import creator_icon_path
from app.services.paths import user_bundles_dir as default_user_bundles_dir
from app.services.printer_discovery import (
    DiscoveredPrinter,
    PrinterDiscoveryError,
    PrinterDiscoveryService,
)
from app.services.preset_catalog import PresetCatalogError, PresetCatalogService
from app.services.project_store import ProjectStoreService
from app.services.renderer import ConfigRenderService
from app.services.saved_connections import SavedConnectionService
from app.services.saved_machine_profiles import SavedMachineProfileService
from app.services.ssh_deploy import SSHDeployError, SSHDeployService
from app.services.ui_scaling import UIScaleMode, UIScalingService
from app.services.validator import ValidationService
from app.services.parity import ParityService
from app.ui.app_state import AppStateStore
from app.ui.design_tokens import build_base_stylesheet, build_files_material_stylesheet
from app.ui.shell_scaffold import BottomStatusBar, LeftNav, RouteDefinition
from app.version import __version__

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


class PrinterDiscoveryWindow(QMainWindow):
    def __init__(self, suggested_cidrs: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scan For Printers")
        self.resize(980, 560)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        discovery_group = QGroupBox("Printer Discovery", root)
        discovery_layout = QVBoxLayout(discovery_group)
        discovery_form = QFormLayout()

        self.scan_cidr_edit = QLineEdit(discovery_group)
        self.scan_cidr_edit.setPlaceholderText("192.168.1.0/24")
        self.scan_cidr_edit.setText(suggested_cidrs[0] if suggested_cidrs else "192.168.1.0/24")
        discovery_form.addRow("IP range", self.scan_cidr_edit)

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

        discovery_hint = QLabel("Scan the network, then choose a host to use for SSH.", discovery_group)
        discovery_hint.setWordWrap(True)
        discovery_layout.addWidget(discovery_hint)

        action_row = QHBoxLayout()
        self.scan_network_btn = QPushButton("Scan Network", discovery_group)
        action_row.addWidget(self.scan_network_btn)
        self.use_selected_host_btn = QPushButton("Use Selected Host", discovery_group)
        action_row.addWidget(self.use_selected_host_btn)
        action_row.addStretch(1)
        discovery_layout.addLayout(action_row)

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
        discovery_layout.addWidget(self.discovery_results_table, 1)

        layout.addWidget(discovery_group, 1)
        self.setCentralWidget(root)


class ActiveConsoleWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Active Console")
        self.resize(1080, 640)

        root = QWidget(self)
        layout = QVBoxLayout(root)

        controls = QHBoxLayout()
        self.clear_btn = QPushButton("Clear", root)
        controls.addWidget(self.clear_btn)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.console_tabs = QTabWidget(root)
        layout.addWidget(self.console_tabs, 1)

        self.active_log = QPlainTextEdit(root)
        self.active_log.setReadOnly(True)
        self.active_log.setMaximumBlockCount(4000)
        self.console_tabs.addTab(self.active_log, "Active")

        self.ssh_log = QPlainTextEdit(root)
        self.ssh_log.setReadOnly(True)
        self.ssh_log.setMaximumBlockCount(2000)
        self.console_tabs.addTab(self.ssh_log, "SSH")

        self.modify_log = QPlainTextEdit(root)
        self.modify_log.setReadOnly(True)
        self.modify_log.setMaximumBlockCount(2000)
        self.console_tabs.addTab(self.modify_log, "Modify Existing")

        self.manage_log = QPlainTextEdit(root)
        self.manage_log.setReadOnly(True)
        self.manage_log.setMaximumBlockCount(2000)
        self.console_tabs.addTab(self.manage_log, "Manage Printer")

        self.setCentralWidget(root)


class PrinterConnectionWindow(QMainWindow):
    def __init__(self, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Printer Connection")
        self.resize(1080, 820)
        self.setCentralWidget(content)


class MainWindow(QMainWindow):
    MACRO_PACK_OPTIONS = {
        "core_maintenance": "Core Maintenance",
        "qgl_helpers": "QGL Helpers",
        "filament_ops": "Filament Ops",
    }

    DEFAULT_VORON_PRESET_ID = "voron_2_4_350"
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
    FILES_EXPERIMENT_SETTING_KEY = "ui/experiments/files_material_v1_enabled"
    # Legacy keys kept only for cleanup migration from older app builds.
    SSH_AUTO_CONNECT_ENABLED_SETTING_KEY = "ui/ssh/auto_connect_enabled"
    SSH_DEFAULT_CONNECTION_SETTING_KEY = "ui/ssh/default_connection_name"
    ADDON_IMPORT_FIELDS = {"addons", "addon_configs"}
    THEME_STYLESHEET_DARK = """
QWidget {
    background-color: #1f1f1f;
    color: #ececec;
}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget, QTreeWidget, QTableWidget, QTabWidget::pane {
    background-color: #2a2a2a;
    color: #ececec;
    border: 1px solid #454545;
}
QPushButton, QToolButton {
    background-color: #343434;
    color: #f0f0f0;
    border: 1px solid #4c4c4c;
    padding: 4px 8px;
}
QPushButton:hover, QToolButton:hover {
    background-color: #3d3d3d;
}
QMenuBar, QMenu {
    background-color: #252525;
    color: #ececec;
}
QMenu {
    min-width: 270px;
}
QMenu::item {
    padding: 6px 40px 6px 10px;
}
QMenu::shortcut {
    padding-right: 6px;
}
QMenu::item:selected {
    background-color: #3a3a3a;
}
QGroupBox {
    border: 1px solid #4a4a4a;
    margin-top: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px 0 4px;
}
"""

    def __init__(
        self,
        ui_scaling_service: UIScalingService | None = None,
        active_scale_mode: UIScaleMode | None = None,
        saved_connection_service: SavedConnectionService | None = None,
        app_settings: QSettings | None = None,
        auto_connect_on_launch: bool = False,
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"KlippConfig v{__version__}")
        self.resize(1380, 900)
        self.app_settings = app_settings or QSettings("KlippConfig", "KlippConfig")

        self.catalog_service = PresetCatalogService()
        self.render_service = ConfigRenderService()
        self.validation_service = ValidationService()
        self.parity_service = ParityService()
        self.firmware_tools_service = FirmwareToolsService()
        self.existing_machine_import_service = ExistingMachineImportService()
        self.action_log_service = ActionLogService()
        self.app_state_store = AppStateStore()
        self.export_service = ExportService()
        self.project_store = ProjectStoreService()
        self.saved_connection_service = saved_connection_service or SavedConnectionService()
        self.saved_machine_profile_service = SavedMachineProfileService()
        self._clear_legacy_ssh_prefs_from_app_settings()
        self.ssh_service: SSHDeployService | None = None
        self.auto_connect_on_launch = bool(auto_connect_on_launch)
        self.auto_connect_enabled = self.saved_connection_service.get_auto_connect_enabled(
            default=True,
        )
        self.default_ssh_connection_name = (
            self.saved_connection_service.get_default_connection_name()
        )
        self.auto_connect_attempted = False
        self.auto_connect_in_progress = False
        self.auto_connect_result_queue: SimpleQueue[dict[str, Any]] = SimpleQueue()
        self.auto_connect_poll_timer = QTimer(self)
        self.auto_connect_poll_timer.setInterval(80)
        self.auto_connect_poll_timer.timeout.connect(self._process_auto_connect_result)
        self.discovery_service = PrinterDiscoveryService()
        self.ui_scaling_service = ui_scaling_service or UIScalingService()
        self.active_scale_mode: UIScaleMode = self.ui_scaling_service.resolve_mode(
            saved=active_scale_mode or self.ui_scaling_service.load_mode()
        )
        self.ui_scale_actions: dict[UIScaleMode, QAction] = {}
        self.ui_scale_action_group: QActionGroup | None = None
        self.addon_options = self._build_addon_options()
        self.ui_routes = [
            RouteDefinition("home", "Home", active=True),
            RouteDefinition("files", "Files", active=True),
            RouteDefinition("generate", "Build", active=True),
            RouteDefinition("printers", "Printers", active=True),
            RouteDefinition("backups", "Backups", active=True),
        ]

        self.presets_by_id: dict[str, Preset] = {}
        self.current_preset: Preset | None = None
        self.current_project: ProjectConfig | None = None
        self.current_pack: RenderedPack | None = None
        self.current_report = ValidationReport()
        self.current_cfg_report = ValidationReport()
        self.current_import_profile: ImportedMachineProfile | None = None
        self.imported_file_map: dict[str, str] = {}
        self.imported_file_order: list[str] = []
        self.import_review_suggestions: list[ImportSuggestion] = []
        self.import_profile_applied_snapshot: dict[str, Any] = {}

        self._applying_project = False
        self._showing_external_file = False
        self.manage_current_remote_file: str | None = None
        self.manage_current_directory: str | None = None
        self.modify_current_remote_file: str | None = None
        self.files_current_content: str = ""
        self.files_current_label: str = ""
        self.files_current_source: str = "generated"
        self.files_current_generated_name: str | None = None
        self.cfg_form_editors: list[dict[str, Any]] = []
        self._last_blocking_alert_snapshot: tuple[str, ...] = ()
        self._last_warning_toast_snapshot: tuple[int, int] = (0, 0)
        self.device_connected = False
        self.active_console_window: ActiveConsoleWindow | None = None
        self.printer_connection_window: PrinterConnectionWindow | None = None
        self.printer_discovery_window: PrinterDiscoveryWindow | None = None
        self.preview_content = ""
        self.preview_source_label = ""
        self.preview_source_kind = "generated"
        self.preview_source_key: str | None = None
        self.preview_pinned = self._settings_bool("ui/persistent_preview_pinned", False)
        pinned_key_raw = self.app_settings.value("ui/persistent_preview_pinned_key", "", type=str)
        self.preview_pinned_key = pinned_key_raw.strip() if pinned_key_raw else None
        self.preview_last_key: str | None = None
        self.preview_collapsed = self._settings_bool("ui/persistent_preview_collapsed", False)
        self.preview_snippet_max_lines = 400
        self.preview_panel_width = self._settings_int("ui/persistent_preview_width", 420)
        self.preview_source_cache: dict[str, dict[str, str]] = {}
        self.preview_validation_cache: dict[str, tuple[int, int]] = {}
        self.preview_connected_printer_name: str | None = None
        self.preview_connected_host: str | None = None
        self.about_window: QMainWindow | None = None
        self.current_project_path: str | None = None
        raw_theme = str(self.app_settings.value("ui/theme_mode", "dark") or "dark").strip().lower()
        self.theme_mode = raw_theme if raw_theme in {"dark", "light"} else "dark"
        self.files_experiment_enabled = self._settings_bool(
            self.FILES_EXPERIMENT_SETTING_KEY,
            False,
        )

        self._build_ui()
        self.app_state_store.update_ui(
            active_route="home",
            legacy_visible=True,
            files_ui_variant=("material_v1" if self.files_experiment_enabled else "classic"),
        )
        self._load_presets()
        self._render_and_validate()

    def _settings_bool(self, key: str, default: bool) -> bool:
        raw = self.app_settings.value(key, default)
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        return text in {"1", "true", "yes", "on"}

    def _settings_int(self, key: str, default: int) -> int:
        raw = self.app_settings.value(key, default)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return max(60, value)

    def _clear_legacy_ssh_prefs_from_app_settings(self) -> None:
        removed = False
        for key in (
            self.SSH_AUTO_CONNECT_ENABLED_SETTING_KEY,
            self.SSH_DEFAULT_CONNECTION_SETTING_KEY,
        ):
            if self.app_settings.contains(key):
                self.app_settings.remove(key)
                removed = True
        if removed:
            self.app_settings.sync()

    def _set_auto_connect_enabled(self, enabled: bool) -> None:
        target = bool(enabled)
        try:
            self.saved_connection_service.set_auto_connect_enabled(target)
        except OSError as exc:
            self._append_ssh_log(f"Failed to persist auto-connect preference: {exc}")
            self.statusBar().showMessage("Failed to save auto-connect preference", 2500)
            target = self.saved_connection_service.get_auto_connect_enabled(default=True)
        self.auto_connect_enabled = target
        if hasattr(self, "ssh_auto_connect_checkbox"):
            self.ssh_auto_connect_checkbox.blockSignals(True)
            self.ssh_auto_connect_checkbox.setChecked(self.auto_connect_enabled)
            self.ssh_auto_connect_checkbox.blockSignals(False)
        self.statusBar().showMessage(
            (
                "Auto-connect on launch enabled."
                if self.auto_connect_enabled
                else "Auto-connect on launch disabled."
            ),
            2500,
        )

    def _persist_default_ssh_connection(self, profile_name: str) -> None:
        target = profile_name.strip()
        try:
            self.saved_connection_service.set_default_connection_name(target)
        except OSError as exc:
            self._append_ssh_log(f"Failed to persist default connection: {exc}")
            self.statusBar().showMessage("Failed to save default connection", 2500)
            target = self.saved_connection_service.get_default_connection_name()
        self.default_ssh_connection_name = target

    def _update_default_connection_ui(self, available_names: list[str] | None = None) -> None:
        if available_names is None:
            try:
                available_names = self.saved_connection_service.list_names()
            except OSError:
                available_names = []

        default_name = self.default_ssh_connection_name.strip()
        if default_name and default_name not in available_names:
            default_name = ""
            self._persist_default_ssh_connection("")

        if hasattr(self, "ssh_default_connection_label"):
            label = default_name if default_name else "(none)"
            self.ssh_default_connection_label.setText(f"Default: {label}")
        if hasattr(self, "ssh_set_default_btn"):
            self.ssh_set_default_btn.setEnabled(bool(available_names))
        if hasattr(self, "ssh_clear_default_btn"):
            self.ssh_clear_default_btn.setEnabled(bool(default_name))

    def _set_default_saved_connection_from_selection(self) -> None:
        if not hasattr(self, "ssh_saved_connection_combo"):
            return
        profile_name = self.ssh_saved_connection_combo.currentText().strip()
        if not profile_name:
            self._show_error("Saved Connections", "No saved connection selected.")
            return
        if self.saved_connection_service.load(profile_name) is None:
            self._show_error("Saved Connections", f"Connection '{profile_name}' was not found.")
            self._refresh_saved_connection_profiles()
            return
        self._persist_default_ssh_connection(profile_name)
        self._update_default_connection_ui()
        self._refresh_tools_connect_menu()
        self._append_ssh_log(f"Set default connection to '{profile_name}'.")
        self.statusBar().showMessage(f"Default connection set: {profile_name}", 2500)

    def _clear_default_saved_connection(self) -> None:
        if not self.default_ssh_connection_name:
            return
        previous = self.default_ssh_connection_name
        self._persist_default_ssh_connection("")
        self._update_default_connection_ui()
        self._refresh_tools_connect_menu()
        self._append_ssh_log(f"Cleared default connection '{previous}'.")
        self.statusBar().showMessage("Default connection cleared", 2500)

    def _is_files_experiment_enabled(self) -> bool:
        return bool(getattr(self, "files_experiment_enabled", False))

    def _set_files_experiment_enabled(self, enabled: bool) -> None:
        self.files_experiment_enabled = bool(enabled)
        self.app_settings.setValue(self.FILES_EXPERIMENT_SETTING_KEY, self.files_experiment_enabled)
        self.app_settings.sync()
        self.action_log_service.log_event(
            "files_experiment_toggle",
            enabled=self.files_experiment_enabled,
        )
        if hasattr(self, "view_files_experiment_action"):
            self.view_files_experiment_action.blockSignals(True)
            self.view_files_experiment_action.setChecked(self.files_experiment_enabled)
            self.view_files_experiment_action.blockSignals(False)
        self.app_state_store.update_ui(
            files_ui_variant=("material_v1" if self.files_experiment_enabled else "classic")
        )
        self._apply_theme_mode(self.theme_mode, persist=False)
        self.statusBar().showMessage(
            (
                "Files UI v1 enabled. Restart to rebuild the Files screen."
                if self.files_experiment_enabled
                else "Files UI v1 disabled. Restart to return to the classic Files screen."
            ),
            3500,
        )

    def _build_addon_options(self) -> dict[str, str]:
        # Add-ons are intentionally disabled in the current app build.
        return {}

    def _build_ui(self) -> None:
        self._build_menu()
        self.top_command_bar = self.menuBar()

        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.toast_anchor = root

        self.tabs = QTabWidget(root)
        self.route_nav_bar = self._build_route_nav_bar(root)
        # Kept for compatibility with existing tests and route metadata checks.
        self.left_nav_scaffold = LeftNav(root)
        self.left_nav_scaffold.set_routes(self.ui_routes)
        self.left_nav_scaffold.hide()
        self.bottom_status_bar = BottomStatusBar(root)
        self.bottom_status_bar.setMinimumHeight(28)

        root_layout.addWidget(self.route_nav_bar)
        root_layout.addWidget(self.tabs, 1)
        root_layout.addWidget(self.bottom_status_bar)

        self._ensure_active_console_window()

        self.main_tab = self._build_main_tab()
        self.wizard_tab = self._build_wizard_tab()
        self.files_tab = (
            self._build_files_tab_experimental()
            if self._is_files_experiment_enabled()
            else self._build_files_tab()
        )
        self.live_deploy_tab = self._build_live_deploy_tab()
        self._ensure_printer_connection_window()
        self.printers_tab = self._build_printers_tab()
        self.modify_existing_tab = self._build_modify_existing_tab()
        self.manage_printer_tab = self._build_manage_printer_tab()

        self.tabs.addTab(self.main_tab, "Main")
        self.tabs.addTab(self.wizard_tab, "Configuration")
        self.tabs.addTab(self.files_tab, "Files")
        self.tabs.addTab(self.printers_tab, "Printers")
        self.tabs.addTab(self.modify_existing_tab, "Modify Existing")
        self.tabs.addTab(self.manage_printer_tab, "Manage Printer")
        self.tabs.tabBar().setVisible(False)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.setCentralWidget(root)
        self._init_toast_notification()
        self._ensure_about_window()
        self._build_footer_connection_health()
        self._set_manage_connected_printer_display(None, None, connected=False)
        self._set_modify_connected_printer_display(None, None, connected=False)
        self._refresh_modify_connection_summary()
        self._refresh_saved_machine_profiles()
        self.app_state_store.subscribe(self._on_app_state_changed)
        self._on_app_state_changed(self.app_state_store.snapshot())
        self.statusBar().showMessage("Ready")

    def _build_route_nav_bar(self, parent: QWidget) -> QWidget:
        bar = QWidget(parent)
        bar.setObjectName("route_nav_bar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 5, 12, 5)
        layout.setSpacing(8)
        self.route_nav_buttons: dict[str, QToolButton] = {}
        self.route_nav_button_group = QButtonGroup(self)
        self.route_nav_button_group.setExclusive(True)

        for route in self.ui_routes:
            if not route.active:
                continue
            button = QToolButton(bar)
            button.setObjectName("route_nav_button")
            button.setText(route.label)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.clicked.connect(
                lambda _checked=False, route_key=route.key: self._on_shell_route_selected(route_key)
            )
            self.route_nav_button_group.addButton(button)
            self.route_nav_buttons[route.key] = button
            layout.addWidget(button)
        layout.addStretch(1)
        self._set_active_route_button("home")
        return bar

    def _set_active_route_button(self, route_key: str) -> None:
        if not hasattr(self, "route_nav_buttons"):
            return
        normalized = (route_key or "").strip().lower()
        if normalized == "edit_config":
            normalized = "files"
        elif normalized in {"deploy", "connect"}:
            normalized = "printers"
        if normalized not in self.route_nav_buttons:
            return
        for key, button in self.route_nav_buttons.items():
            button.blockSignals(True)
            button.setChecked(key == normalized)
            button.blockSignals(False)

    def _ensure_active_console_window(self) -> ActiveConsoleWindow:
        if self.active_console_window is not None:
            return self.active_console_window

        console_window = ActiveConsoleWindow(parent=self)
        console_window.clear_btn.clicked.connect(self._clear_active_console_logs)
        self.active_console_window = console_window
        self.console_activity_log = console_window.active_log
        self.ssh_log = console_window.ssh_log
        self.modify_log = console_window.modify_log
        self.manage_log = console_window.manage_log
        return console_window

    def _clear_active_console_logs(self) -> None:
        if hasattr(self, "console_activity_log"):
            self.console_activity_log.clear()
        if hasattr(self, "ssh_log"):
            self.ssh_log.clear()
        if hasattr(self, "modify_log"):
            self.modify_log.clear()
        if hasattr(self, "manage_log"):
            self.manage_log.clear()
        self.statusBar().showMessage("Console cleared", 2000)

    def _open_active_console_window(self) -> None:
        console_window = self._ensure_active_console_window()
        console_window.show()
        console_window.raise_()
        console_window.activateWindow()
        self.statusBar().showMessage("Opened active console", 2500)

    def _ensure_printer_connection_window(self) -> PrinterConnectionWindow:
        if self.printer_connection_window is not None:
            return self.printer_connection_window
        connection_window = PrinterConnectionWindow(self.live_deploy_tab, parent=self)
        self.printer_connection_window = connection_window
        return connection_window

    def _open_printer_connection_window(self, *, active_route: str = "deploy") -> None:
        connection_window = self._ensure_printer_connection_window()
        connection_window.show()
        connection_window.raise_()
        connection_window.activateWindow()
        self.app_state_store.update_ui(active_route=active_route, right_panel_mode="context")
        self.statusBar().showMessage("Opened printer connection window", 2500)

    def _open_printers_webview_or_setup(self) -> None:
        self.app_state_store.update_ui(active_route="printers", right_panel_mode="context")
        if not self._has_ssh_target_configured():
            self._open_printer_connection_window(active_route="printers")
            self.statusBar().showMessage(
                "Set SSH host and username to configure a printer first.",
                3500,
            )
            return
        self.tabs.setCurrentWidget(self.printers_tab)
        self._manage_open_control_window()
        self.app_state_store.update_ui(active_route="printers", right_panel_mode="context")

    def _init_toast_notification(self) -> None:
        self.toast_notification = QLabel("", self.toast_anchor)
        self.toast_notification.setObjectName("toast_notification")
        self.toast_notification.setWordWrap(True)
        self.toast_notification.setVisible(False)
        self.toast_notification.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.toast_notification.setStyleSheet(
            "QLabel {"
            " background-color: #111827;"
            " color: #e5e7eb;"
            " border: 1px solid #374151;"
            " border-radius: 6px;"
            " padding: 8px 10px;"
            " font-weight: 600;"
            "}"
        )
        self.toast_hide_timer = QTimer(self)
        self.toast_hide_timer.setSingleShot(True)
        self.toast_hide_timer.timeout.connect(self._hide_toast_notification)

    def _hide_toast_notification(self) -> None:
        if hasattr(self, "toast_notification"):
            self.toast_notification.setVisible(False)

    def _position_toast_notification(self) -> None:
        if not hasattr(self, "toast_notification"):
            return
        parent = self.toast_notification.parentWidget()
        if parent is None:
            return
        margin = 14
        max_width = max(240, min(420, int(parent.width() * 0.42)))
        self.toast_notification.setFixedWidth(max_width)
        hint = self.toast_notification.sizeHint()
        self.toast_notification.resize(max_width, hint.height())
        x = parent.width() - self.toast_notification.width() - margin
        y = parent.height() - self.toast_notification.height() - margin
        self.toast_notification.move(max(margin, x), max(margin, y))

    def _show_toast_notification(
        self,
        message: str,
        *,
        severity: str = "info",
        duration_ms: int = 4500,
    ) -> None:
        if not hasattr(self, "toast_notification"):
            return
        text = " ".join(str(message).split()).strip()
        if not text:
            return
        style_by_severity = {
            "warning": (
                "QLabel {"
                " background-color: #7f1d1d;"
                " color: #ffffff;"
                " border: 1px solid #ef4444;"
                " border-radius: 6px;"
                " padding: 8px 10px;"
                " font-weight: 600;"
                "}"
            ),
            "caution": (
                "QLabel {"
                " background-color: #78350f;"
                " color: #ffffff;"
                " border: 1px solid #f59e0b;"
                " border-radius: 6px;"
                " padding: 8px 10px;"
                " font-weight: 600;"
                "}"
            ),
            "info": (
                "QLabel {"
                " background-color: #111827;"
                " color: #e5e7eb;"
                " border: 1px solid #374151;"
                " border-radius: 6px;"
                " padding: 8px 10px;"
                " font-weight: 600;"
                "}"
            ),
        }
        self.toast_notification.setText(text)
        self.toast_notification.setStyleSheet(style_by_severity.get(severity, style_by_severity["info"]))
        self._position_toast_notification()
        self.toast_notification.setVisible(True)
        self.toast_notification.raise_()
        self.toast_hide_timer.start(max(1500, duration_ms))

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self._position_toast_notification()

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if not self.auto_connect_on_launch or self.auto_connect_attempted:
            return
        self.auto_connect_attempted = True
        QTimer.singleShot(250, self._attempt_auto_connect_saved_profile)

    def _attempt_auto_connect_saved_profile(self) -> None:
        if self.device_connected or self.auto_connect_in_progress:
            return
        if not self.auto_connect_enabled:
            self._append_ssh_log("Auto-connect skipped: disabled in SSH settings.")
            return

        try:
            saved_profiles = self.saved_connection_service.list_names()
        except OSError as exc:
            self._append_ssh_log(f"Auto-connect skipped: failed to read saved connections ({exc}).")
            return
        if not saved_profiles:
            return

        default_name = self.default_ssh_connection_name.strip()
        profile_name = ""
        if len(saved_profiles) == 1:
            profile_name = saved_profiles[0]
        elif default_name and default_name in saved_profiles:
            profile_name = default_name
        else:
            self._append_ssh_log(
                "Auto-connect skipped: multiple saved connections found. Set a default connection."
            )
            self.statusBar().showMessage(
                "Auto-connect skipped: set a default saved connection.",
                4000,
            )
            return

        if not self._load_saved_connection_profile(profile_name):
            return

        params = self._collect_ssh_params(show_errors=False)
        if params is None:
            self._append_ssh_log(
                f"Auto-connect skipped for '{profile_name}': host/username or key path is invalid."
            )
            return

        service = self._get_ssh_service(show_errors=False)
        if service is None:
            self._append_ssh_log("Auto-connect skipped: SSH service is unavailable.")
            return

        self.auto_connect_in_progress = True
        self._update_action_enablement()
        self.statusBar().showMessage(f"Auto-connecting to {params['host']}...", 0)
        self._append_ssh_log(
            f"Auto-connect: {params['username']}@{params['host']}:{params['port']} ({profile_name})"
        )
        self.action_log_service.log_event(
            "connect",
            phase="start",
            host=str(params["host"]),
            username=str(params["username"]),
            port=int(params["port"]),
            source="startup",
            profile_name=profile_name,
        )

        def _run_connect() -> None:
            try:
                ok, output = service.test_connection(**params)
                self.auto_connect_result_queue.put(
                    {
                        "ok": bool(ok),
                        "output": str(output),
                        "params": params,
                        "profile_name": profile_name,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self.auto_connect_result_queue.put(
                    {
                        "ok": False,
                        "output": str(exc),
                        "params": params,
                        "profile_name": profile_name,
                    }
                )

        threading.Thread(target=_run_connect, name="klippconfig-auto-connect", daemon=True).start()
        self.auto_connect_poll_timer.start()

    def _process_auto_connect_result(self) -> None:
        if not self.auto_connect_in_progress:
            self.auto_connect_poll_timer.stop()
            return

        try:
            result = self.auto_connect_result_queue.get_nowait()
        except Empty:
            return

        self.auto_connect_in_progress = False
        self.auto_connect_poll_timer.stop()

        params_raw = result.get("params")
        params = params_raw if isinstance(params_raw, dict) else {}
        host = str(params.get("host") or "").strip()
        output = str(result.get("output") or "").strip() or "No response."
        ok = bool(result.get("ok"))

        if ok:
            self._apply_connect_success(params, output, source="startup")
            return

        self._apply_connect_failure(
            params,
            output,
            source="startup",
            show_error_dialog=False,
            use_failure_prefix=False,
        )
        if host:
            self.statusBar().showMessage(f"Auto-connect failed for {host}", 4000)
        else:
            self.statusBar().showMessage("Auto-connect failed", 4000)

    def _build_persistent_preview_panel(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("Persistent Preview", panel)
        title.setStyleSheet("QLabel { font-weight: 700; }")
        header.addWidget(title)
        header.addStretch(1)
        self.preview_collapse_btn = QToolButton(panel)
        self.preview_collapse_btn.setText("Collapse")
        self.preview_collapse_btn.clicked.connect(self._toggle_preview_collapsed)
        header.addWidget(self.preview_collapse_btn)
        layout.addLayout(header)

        self.preview_content_container = QWidget(panel)
        content_layout = QVBoxLayout(self.preview_content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)

        self.preview_source_label_widget = QLabel("No active file preview.", self.preview_content_container)
        self.preview_source_label_widget.setWordWrap(True)
        content_layout.addWidget(self.preview_source_label_widget)

        badges = QHBoxLayout()
        self.preview_kind_badge = QLabel("Source: none", self.preview_content_container)
        self.preview_validation_badge = QLabel("Validation: n/a", self.preview_content_container)
        self.preview_connection_badge = QLabel("Device: disconnected", self.preview_content_container)
        badges.addWidget(self.preview_kind_badge)
        badges.addWidget(self.preview_validation_badge)
        badges.addWidget(self.preview_connection_badge)
        badges.addStretch(1)
        content_layout.addLayout(badges)

        self.preview_text = QPlainTextEdit(self.preview_content_container)
        self.preview_text.setReadOnly(True)
        self.preview_text.setPlaceholderText("No active file preview. Open or generate a .cfg file.")
        content_layout.addWidget(self.preview_text, 1)

        actions = QHBoxLayout()
        self.preview_open_in_files_btn = QPushButton("Open in Files", self.preview_content_container)
        self.preview_open_in_files_btn.clicked.connect(self._preview_open_in_files)
        actions.addWidget(self.preview_open_in_files_btn)

        self.preview_validate_btn = QPushButton("Validate", self.preview_content_container)
        self.preview_validate_btn.clicked.connect(self._preview_validate_current)
        actions.addWidget(self.preview_validate_btn)

        self.preview_refactor_btn = QPushButton("Refactor", self.preview_content_container)
        self.preview_refactor_btn.clicked.connect(self._preview_refactor_current)
        actions.addWidget(self.preview_refactor_btn)

        self.preview_pin_btn = QPushButton("Pin", self.preview_content_container)
        self.preview_pin_btn.clicked.connect(self._preview_toggle_pin)
        actions.addWidget(self.preview_pin_btn)

        self.preview_copy_path_btn = QPushButton("Copy Path", self.preview_content_container)
        self.preview_copy_path_btn.clicked.connect(self._preview_copy_path)
        actions.addWidget(self.preview_copy_path_btn)
        actions.addStretch(1)
        content_layout.addLayout(actions)
        layout.addWidget(self.preview_content_container, 1)

        self._set_preview_badge_style(self.preview_kind_badge, "#111827", "#374151")
        self._set_preview_badge_style(self.preview_validation_badge, "#111827", "#374151")
        self._set_preview_badge_style(self.preview_connection_badge, "#111827", "#374151")
        self._update_preview_action_enablement()
        return panel

    @staticmethod
    def _set_preview_badge_style(label: QLabel, background: str, border: str) -> None:
        label.setStyleSheet(
            "QLabel {"
            f" background-color: {background};"
            " color: #e5e7eb;"
            f" border: 1px solid {border};"
            " border-radius: 4px;"
            " padding: 2px 6px;"
            "}"
        )

    def _apply_preview_splitter_width(self, width: int) -> None:
        if not hasattr(self, "main_content_splitter"):
            return
        sizes = self.main_content_splitter.sizes()
        if len(sizes) < 2:
            return
        total = sum(sizes)
        if total <= 0:
            total = max(self.width(), 800)
        safe_width = max(120, min(width, total - 120))
        self.main_content_splitter.setSizes([max(120, total - safe_width), safe_width])

    def _on_main_splitter_moved(self, _pos: int, _index: int) -> None:
        if self.preview_collapsed:
            return
        sizes = self.main_content_splitter.sizes()
        if len(sizes) < 2:
            return
        width = max(120, int(sizes[1]))
        self.preview_panel_width = width
        self._persist_preview_settings()

    def _toggle_preview_collapsed(self) -> None:
        self._set_preview_collapsed(not self.preview_collapsed)

    def _toggle_persistent_preview_action(self, checked: bool) -> None:
        self._set_preview_collapsed(not checked)

    def _set_preview_collapsed(self, collapsed: bool, *, persist: bool = True) -> None:
        self.preview_collapsed = collapsed
        if hasattr(self, "preview_content_container"):
            self.preview_content_container.setVisible(not collapsed)
        if hasattr(self, "preview_collapse_btn"):
            self.preview_collapse_btn.setText("Expand" if collapsed else "Collapse")
        if hasattr(self, "preview_toggle_action"):
            self.preview_toggle_action.blockSignals(True)
            self.preview_toggle_action.setChecked(not collapsed)
            self.preview_toggle_action.setText(
                "Show Persistent Preview" if collapsed else "Hide Persistent Preview"
            )
            self.preview_toggle_action.blockSignals(False)

        if hasattr(self, "main_content_splitter"):
            if collapsed:
                sizes = self.main_content_splitter.sizes()
                total = sum(sizes) if sizes else max(self.width(), 800)
                self.main_content_splitter.setSizes([max(120, total - 60), 60])
            else:
                self._apply_preview_splitter_width(self.preview_panel_width)

        if persist:
            self._persist_preview_settings()

    def _on_tab_changed(self, _index: int) -> None:
        self._refresh_persistent_preview_for_tab_change()
        current = self.tabs.currentWidget()
        route = "home"
        if current is self.main_tab:
            route = "home"
        elif current is self.files_tab:
            route = "files"
        elif current is self.modify_existing_tab:
            route = "edit_config"
        elif current is self.wizard_tab:
            route = "generate"
        elif current is self.printers_tab:
            route = "printers"
        elif current is self.manage_printer_tab:
            route = "backups"
        self.app_state_store.update_ui(active_route=route)
        self._set_active_route_button(route)

    def _on_shell_route_selected(self, route_key: str) -> None:
        route = (route_key or "").strip().lower()
        if route == "legacy":
            route = "home"
        if route == "home":
            self.tabs.setCurrentWidget(self.main_tab)
            self.app_state_store.update_ui(
                active_route=route,
                right_panel_mode="context",
            )
            return
        if route == "connect":
            self._open_printer_connection_window(active_route="printers")
            return
        if route == "files":
            self.tabs.setCurrentWidget(self.files_tab)
            self.app_state_store.update_ui(active_route=route, right_panel_mode="context")
            return
        if route == "edit_config":
            self.tabs.setCurrentWidget(self.files_tab)
            if hasattr(self, "validation_section_toggle"):
                self.validation_section_toggle.setChecked(True)
            self.app_state_store.update_ui(active_route=route, right_panel_mode="validation")
            return
        if route == "generate":
            self.tabs.setCurrentWidget(self.wizard_tab)
            self.app_state_store.update_ui(active_route=route, right_panel_mode="context")
            return
        if route == "deploy":
            self._open_printer_connection_window(active_route="printers")
            return
        if route == "printers":
            self._open_printers_webview_or_setup()
            return
        if route == "backups":
            self.tabs.setCurrentWidget(self.manage_printer_tab)
            self.app_state_store.update_ui(active_route=route, right_panel_mode="context")
            return

    def _on_app_state_changed(self, state) -> None:  # noqa: ANN001
        ui = state.ui
        if hasattr(self, "view_toggle_sidebar_action"):
            self.view_toggle_sidebar_action.blockSignals(True)
            self.view_toggle_sidebar_action.setChecked(bool(ui.left_nav_visible))
            self.view_toggle_sidebar_action.blockSignals(False)
        if hasattr(self, "route_nav_bar"):
            self.route_nav_bar.setVisible(bool(ui.left_nav_visible))
        self._set_active_route_button(ui.active_route or "home")
        if hasattr(self, "view_right_panel_context_action"):
            self.view_right_panel_context_action.setChecked(ui.right_panel_mode == "context")
        if hasattr(self, "view_right_panel_validation_action"):
            self.view_right_panel_validation_action.setChecked(ui.right_panel_mode == "validation")
        if hasattr(self, "view_right_panel_logs_action"):
            self.view_right_panel_logs_action.setChecked(ui.right_panel_mode == "logs")
        if hasattr(self, "view_files_experiment_action"):
            self.view_files_experiment_action.blockSignals(True)
            self.view_files_experiment_action.setChecked(ui.files_ui_variant == "material_v1")
            self.view_files_experiment_action.blockSignals(False)

        if hasattr(self, "right_context_panel"):
            mode = (ui.right_panel_mode or "context").strip().lower()
            if mode == "validation":
                panel_lines = [
                    "Validation",
                    "",
                    f"Blocking: {state.validation.blocking}",
                    f"Warnings: {state.validation.warnings}",
                    f"Source: {state.validation.source_label or 'n/a'}",
                    "",
                    "Tip: Use Configuration -> Validate Current for full diagnostics.",
                ]
            elif mode == "logs":
                recent_logs: list[str] = []
                if hasattr(self, "ssh_log"):
                    ssh_lines = self.ssh_log.toPlainText().splitlines()
                    if ssh_lines:
                        recent_logs.extend(ssh_lines[-8:])
                if hasattr(self, "modify_log"):
                    modify_lines = self.modify_log.toPlainText().splitlines()
                    if modify_lines:
                        recent_logs.extend(modify_lines[-6:])
                if not recent_logs:
                    recent_logs = ["(no console log entries yet)"]
                panel_lines = ["Logs", ""] + recent_logs[-14:]
            else:
                panel_lines = ["Context", "", f"Route: {ui.active_route or 'home'}"]
                if ui.active_route == "generate" and self.current_project is not None:
                    project = self.current_project
                    panel_lines.extend(
                        [
                            "",
                            "Machine Summary",
                            f"Preset: {project.preset_id}",
                            f"Board: {project.board}",
                            (
                                f"Build volume: {project.dimensions.x} x {project.dimensions.y} x "
                                f"{project.dimensions.z}"
                            ),
                            f"Probe: {project.probe.type or 'None'}",
                            f"Toolhead: {project.toolhead.board or 'None'}",
                            "Add-ons: disabled",
                        ]
                    )
                else:
                    panel_lines.extend(
                        [
                            "",
                            f"Connected: {'yes' if state.connection.connected else 'no'}",
                            f"Host: {state.connection.host or 'n/a'}",
                            f"Printer: {state.connection.target_printer or 'n/a'}",
                            "",
                            f"Active file: {state.active_file.path or 'none'}",
                            f"Source: {state.active_file.source or 'n/a'}",
                            f"Dirty: {'yes' if state.active_file.dirty else 'no'}",
                            "",
                            f"Upload: {'busy' if state.deploy.upload_in_progress else 'idle'}",
                            f"Upload status: {state.deploy.last_upload_status or 'n/a'}",
                            f"Restart status: {state.deploy.last_restart_status or 'n/a'}",
                        ]
                    )
            self.right_context_panel.content.setPlainText("\n".join(panel_lines))
        self.bottom_status_bar.set_connection(
            state.connection.connected,
            state.connection.target_printer or state.connection.host,
        )
        if state.deploy.upload_in_progress:
            state_text = "uploading"
        elif state.validation.blocking > 0:
            state_text = f"blocked ({state.validation.blocking})"
        elif state.validation.warnings > 0:
            state_text = f"warnings ({state.validation.warnings})"
        else:
            state_text = "ready"
        self.bottom_status_bar.set_state(state_text)
        self._update_files_experiment_chips(
            blocking=state.validation.blocking,
            warnings=state.validation.warnings,
            source_label=state.validation.source_label,
        )
        self._update_action_enablement()

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
        host = self.ssh_host_edit.text().strip() if hasattr(self, "ssh_host_edit") else ""
        printer_name = self.preview_connected_printer_name or ""
        profile_name = self.ssh_connection_name_edit.text().strip() if hasattr(
            self, "ssh_connection_name_edit"
        ) else ""
        self.app_state_store.update_connection(
            connected=connected,
            host=host,
            target_printer=printer_name,
            profile_name=profile_name,
        )
        if hasattr(self, "bottom_status_bar"):
            self.bottom_status_bar.set_connection(connected, printer_name or host)
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
        self._update_preview_connection_badge()

    def _set_connected_printer_display_label(
        self,
        label_widget: QLabel,
        printer_name: str | None,
        host: str | None,
        *,
        connected: bool,
    ) -> None:
        if connected and printer_name:
            label = printer_name
            clean_host = (host or "").strip()
            if clean_host and clean_host.casefold() != printer_name.casefold():
                label = f"{printer_name} ({clean_host})"
            label_widget.setText(label)
            label_widget.setStyleSheet(
                "QLabel {"
                " background-color: #14532d;"
                " color: #ffffff;"
                " border: 1px solid #16a34a;"
                " border-radius: 4px;"
                " padding: 4px 6px;"
                " font-weight: 600;"
                "}"
            )
            return
        label_widget.setText("No active SSH connection.")
        label_widget.setStyleSheet(
            "QLabel {"
            " background-color: #111827;"
            " color: #e5e7eb;"
            " border: 1px solid #374151;"
            " border-radius: 4px;"
            " padding: 4px 6px;"
            "}"
        )

    def _set_manage_connected_printer_display(
        self,
        printer_name: str | None,
        host: str | None,
        *,
        connected: bool,
    ) -> None:
        if not hasattr(self, "manage_connected_printer_label"):
            return
        self._set_connected_printer_display_label(
            self.manage_connected_printer_label,
            printer_name,
            host,
            connected=connected,
        )

    def _set_modify_connected_printer_display(
        self,
        printer_name: str | None,
        host: str | None,
        *,
        connected: bool,
    ) -> None:
        if not hasattr(self, "modify_connected_printer_label"):
            return
        self._set_connected_printer_display_label(
            self.modify_connected_printer_label,
            printer_name,
            host,
            connected=connected,
        )

    def _build_preview_source_key(
        self,
        source_kind: str,
        label: str,
        generated_name: str | None = None,
    ) -> str:
        normalized_kind = (source_kind or "generated").strip().lower() or "generated"
        if normalized_kind == "generated":
            value = (generated_name or "").strip()
            if not value and label.lower().startswith("generated:"):
                value = label.split(":", 1)[1].strip()
            value = value or "printer.cfg"
            return f"generated:{value}"
        if normalized_kind == "remote" and label.lower().startswith("remote:"):
            value = label.split(":", 1)[1].strip()
            return f"remote:{value}"
        return f"{normalized_kind}:{label.strip()}"

    @staticmethod
    def _extract_preview_path_from_label(label: str) -> str:
        text = label.strip()
        if ":" in text:
            prefix, rest = text.split(":", 1)
            if prefix.strip().lower() in {"generated", "remote"} and rest.strip():
                return rest.strip()
        return text

    def _set_preview_validation_state(
        self,
        source_key: str,
        *,
        blocking: int,
        warnings: int,
    ) -> None:
        self.preview_validation_cache[source_key] = (blocking, warnings)
        if self.preview_source_key == source_key:
            self._update_preview_validation_badge(source_key)

    def _set_persistent_preview_source(
        self,
        *,
        content: str,
        label: str,
        source_kind: str,
        generated_name: str | None = None,
        source_key: str | None = None,
        update_last: bool = True,
    ) -> str:
        key = source_key or self._build_preview_source_key(source_kind, label, generated_name)
        self.preview_source_cache[key] = {
            "content": content,
            "label": label,
            "kind": source_kind,
            "path": self._extract_preview_path_from_label(label),
        }
        if update_last:
            self.preview_last_key = key

        if self.preview_pinned and self.preview_pinned_key and self.preview_pinned_key != key:
            self._refresh_persistent_preview_for_tab_change()
            return key

        self._apply_preview_source(key)
        return key

    def _apply_preview_source(self, source_key: str) -> None:
        entry = self.preview_source_cache.get(source_key)
        if entry is None:
            self._show_empty_preview()
            return

        content = entry.get("content", "")
        label = entry.get("label", "")
        kind = entry.get("kind", "generated")
        self.preview_content = content
        self.preview_source_label = label
        self.preview_source_kind = kind
        self.preview_source_key = source_key

        if hasattr(self, "preview_source_label_widget"):
            self.preview_source_label_widget.setText(label or "No active file preview.")
        if hasattr(self, "preview_kind_badge"):
            self.preview_kind_badge.setText(f"Source: {kind}")

        snippet = self._render_preview_snippet(content)
        if hasattr(self, "preview_text"):
            self.preview_text.setPlainText(snippet)

        self._update_preview_validation_badge(source_key)
        self._update_preview_connection_badge()
        self._update_preview_action_enablement()

    def _show_empty_preview(self) -> None:
        self.preview_content = ""
        self.preview_source_label = "No active file preview. Open or generate a .cfg file."
        self.preview_source_kind = "none"
        self.preview_source_key = None
        if hasattr(self, "preview_source_label_widget"):
            self.preview_source_label_widget.setText(self.preview_source_label)
        if hasattr(self, "preview_kind_badge"):
            self.preview_kind_badge.setText("Source: none")
        if hasattr(self, "preview_text"):
            self.preview_text.setPlainText("No active file preview. Open or generate a .cfg file.")
        if hasattr(self, "preview_validation_badge"):
            self.preview_validation_badge.setText("Validation: n/a")
            self._set_preview_badge_style(self.preview_validation_badge, "#111827", "#374151")
        self._update_preview_connection_badge()
        self._update_preview_action_enablement()

    def _resolve_preview_fallback(self) -> str | None:
        if self.preview_pinned and self.preview_pinned_key:
            if self.preview_pinned_key in self.preview_source_cache:
                return self.preview_pinned_key

        if self.preview_last_key and self.preview_last_key in self.preview_source_cache:
            return self.preview_last_key

        if self.current_pack is not None:
            printer_cfg = self.current_pack.files.get("printer.cfg")
            if printer_cfg:
                key = "generated:printer.cfg"
                self.preview_source_cache[key] = {
                    "content": printer_cfg,
                    "label": "Generated: printer.cfg",
                    "kind": "generated",
                    "path": "printer.cfg",
                }
                return key
        return None

    def _refresh_persistent_preview_for_tab_change(self) -> None:
        key = self._resolve_preview_fallback()
        if key is None:
            self._show_empty_preview()
            return
        self._apply_preview_source(key)

    def _render_preview_snippet(self, content: str) -> str:
        lines = content.splitlines()
        if len(lines) <= self.preview_snippet_max_lines:
            return content
        clipped = "\n".join(lines[: self.preview_snippet_max_lines])
        return (
            f"[Preview truncated to first {self.preview_snippet_max_lines} lines]\n\n"
            f"{clipped}\n"
        )

    def _is_preview_cfg_source(self) -> bool:
        key = self.preview_source_key
        if not key:
            return False
        entry = self.preview_source_cache.get(key, {})
        label = entry.get("label", "")
        path = entry.get("path", "")
        return self._is_cfg_label(label, None) or path.lower().endswith(".cfg")

    def _update_preview_validation_badge(self, source_key: str | None) -> None:
        if not hasattr(self, "preview_validation_badge"):
            return
        if not source_key or source_key not in self.preview_validation_cache:
            self.preview_validation_badge.setText("Validation: n/a")
            self._set_preview_badge_style(self.preview_validation_badge, "#111827", "#374151")
            return

        blocking, warnings = self.preview_validation_cache[source_key]
        if blocking > 0:
            self.preview_validation_badge.setText(f"Validation: {blocking} blocking / {warnings} warning")
            self._set_preview_badge_style(self.preview_validation_badge, "#7f1d1d", "#ef4444")
            return
        if warnings > 0:
            self.preview_validation_badge.setText(f"Validation: warnings ({warnings})")
            self._set_preview_badge_style(self.preview_validation_badge, "#78350f", "#f59e0b")
            return
        self.preview_validation_badge.setText("Validation: clean")
        self._set_preview_badge_style(self.preview_validation_badge, "#14532d", "#16a34a")

    def _update_preview_connection_badge(self) -> None:
        if not hasattr(self, "preview_connection_badge"):
            return
        if self.device_connected:
            label = "Device: connected"
            if self.preview_connected_printer_name:
                label = f"Device: {self.preview_connected_printer_name}"
                if (
                    self.preview_connected_host
                    and self.preview_connected_host.casefold()
                    != self.preview_connected_printer_name.casefold()
                ):
                    label = f"{label} ({self.preview_connected_host})"
            self.preview_connection_badge.setText(label)
            self._set_preview_badge_style(self.preview_connection_badge, "#14532d", "#16a34a")
            return
        self.preview_connection_badge.setText("Device: disconnected")
        self._set_preview_badge_style(self.preview_connection_badge, "#111827", "#374151")

    def _update_preview_action_enablement(self) -> None:
        has_source = bool(self.preview_source_key and self.preview_source_key in self.preview_source_cache)
        is_cfg = has_source and self._is_preview_cfg_source()
        if hasattr(self, "preview_open_in_files_btn"):
            self.preview_open_in_files_btn.setEnabled(has_source)
        if hasattr(self, "preview_validate_btn"):
            self.preview_validate_btn.setEnabled(is_cfg)
        if hasattr(self, "preview_refactor_btn"):
            self.preview_refactor_btn.setEnabled(is_cfg)
        if hasattr(self, "preview_copy_path_btn"):
            self.preview_copy_path_btn.setEnabled(has_source)
        if hasattr(self, "preview_pin_btn"):
            self.preview_pin_btn.setEnabled(has_source)
            self.preview_pin_btn.setText("Unpin" if self.preview_pinned else "Pin")

    def _preview_toggle_pin(self) -> None:
        if not self.preview_source_key:
            return
        if self.preview_pinned and self.preview_pinned_key == self.preview_source_key:
            self.preview_pinned = False
            self.preview_pinned_key = None
        else:
            self.preview_pinned = True
            self.preview_pinned_key = self.preview_source_key
        self._persist_preview_settings()
        self._update_preview_action_enablement()
        self._refresh_persistent_preview_for_tab_change()

    def _preview_copy_path(self) -> None:
        if not self.preview_source_key:
            return
        entry = self.preview_source_cache.get(self.preview_source_key)
        if entry is None:
            return
        value = entry.get("path", "") or entry.get("label", "")
        QApplication.clipboard().setText(value)
        self.statusBar().showMessage("Preview path copied", 2000)

    def _preview_open_in_files(self) -> None:
        if not self.preview_source_key:
            return
        entry = self.preview_source_cache.get(self.preview_source_key)
        if entry is None:
            return

        content = entry.get("content", "")
        label = entry.get("label", "")
        kind = entry.get("kind", "generated")
        path = entry.get("path", "")

        if kind == "generated":
            file_name = path or "printer.cfg"
            if self.current_pack is not None and file_name in self.current_pack.files:
                matches = self.generated_file_list.findItems(file_name, Qt.MatchFlag.MatchExactly)
                if matches:
                    self.generated_file_list.setCurrentItem(matches[0])
                    self._showing_external_file = False
                    self._show_selected_generated_file()
                else:
                    self._set_files_tab_content(
                        content=content,
                        label=f"Generated: {file_name}",
                        source="generated",
                        generated_name=file_name,
                    )
            else:
                self._set_files_tab_content(
                    content=content,
                    label=f"Generated: {file_name}",
                    source="generated",
                    generated_name=file_name,
                )
        else:
            self._showing_external_file = True
            if kind in {"manage_remote", "modify_remote"}:
                label = f"Remote: {path}" if path else label
            self._set_files_tab_content(
                content=content,
                label=label,
                source="remote" if kind.endswith("remote") else kind,
                generated_name=None,
            )
        self.tabs.setCurrentWidget(self.files_tab)
        self._refresh_persistent_preview_for_tab_change()

    def _preview_validate_current(self) -> None:
        if not self._is_preview_cfg_source():
            return
        key = self.preview_source_key
        if key and key.startswith("manage_remote:"):
            self._manage_validate_current_file()
            return
        if key and key.startswith("modify_remote:"):
            self._modify_validate_current_file()
            return
        self._preview_open_in_files()
        self._run_current_cfg_validation(show_dialog=False)

    def _preview_refactor_current(self) -> None:
        if not self._is_preview_cfg_source():
            return
        key = self.preview_source_key
        if key and key.startswith("manage_remote:"):
            self._manage_refactor_current_file()
            return
        if key and key.startswith("modify_remote:"):
            self._modify_refactor_current_file()
            return
        self._preview_open_in_files()
        self._refactor_current_cfg_file()

    def _persist_preview_settings(self) -> None:
        self.app_settings.setValue("ui/persistent_preview_collapsed", self.preview_collapsed)
        self.app_settings.setValue("ui/persistent_preview_pinned", self.preview_pinned)
        self.app_settings.setValue("ui/persistent_preview_pinned_key", self.preview_pinned_key or "")
        self.app_settings.setValue("ui/persistent_preview_width", self.preview_panel_width)
        self.app_settings.sync()

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()
        menu_bar.clear()

        file_menu = menu_bar.addMenu("File")
        self.file_new_machine_action = QAction("New Machine Profile", self)
        self.file_new_machine_action.triggered.connect(self._new_project)
        file_menu.addAction(self.file_new_machine_action)

        self.file_open_machine_action = QAction("Open Machine Profile...", self)
        self.file_open_machine_action.setShortcut("Ctrl+O")
        self.file_open_machine_action.triggered.connect(self._load_project_from_file)
        file_menu.addAction(self.file_open_machine_action)

        self.file_save_action = QAction("Save", self)
        self.file_save_action.setShortcut("Ctrl+S")
        self.file_save_action.triggered.connect(self._save_project)
        file_menu.addAction(self.file_save_action)

        self.file_save_as_action = QAction("Save As...", self)
        self.file_save_as_action.setShortcut("Ctrl+Shift+S")
        self.file_save_as_action.triggered.connect(self._save_project_to_file)
        file_menu.addAction(self.file_save_as_action)
        file_menu.addSeparator()

        self.file_import_preset_action = QAction("Import Preset...", self)
        self.file_import_preset_action.triggered.connect(self._import_preset_placeholder)
        file_menu.addAction(self.file_import_preset_action)

        self.file_export_generated_pack_action = QAction("Export Generated Pack...", self)
        self.file_export_generated_pack_action.triggered.connect(self._export_generated_pack_dialog)
        file_menu.addAction(self.file_export_generated_pack_action)

        # Retain direct export actions for existing workflows and test hooks.
        self.export_folder_action = QAction("Export Folder...", self)
        self.export_folder_action.triggered.connect(self._export_folder)
        self.export_zip_action = QAction("Export ZIP...", self)
        self.export_zip_action.triggered.connect(self._export_zip)
        export_options_menu = file_menu.addMenu("Export Options")
        export_options_menu.addAction(self.export_folder_action)
        export_options_menu.addAction(self.export_zip_action)

        file_menu.addSeparator()
        self.file_settings_action = QAction("Settings", self)
        self.file_settings_action.triggered.connect(self._open_settings_dialog)
        file_menu.addAction(self.file_settings_action)
        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menu_bar.addMenu("View")
        self.view_toggle_sidebar_action = QAction("Toggle Navigation Bar", self)
        self.view_toggle_sidebar_action.setCheckable(True)
        self.view_toggle_sidebar_action.setChecked(True)
        self.view_toggle_sidebar_action.toggled.connect(self._set_sidebar_visible)
        view_menu.addAction(self.view_toggle_sidebar_action)

        self.view_toggle_raw_form_action = QAction("Show Raw / Form Mode", self)
        self.view_toggle_raw_form_action.triggered.connect(self._toggle_raw_form_mode)
        view_menu.addAction(self.view_toggle_raw_form_action)

        self.view_theme_menu = view_menu.addMenu("Theme (Dark/Light)")
        self.view_theme_group = QActionGroup(self)
        self.view_theme_group.setExclusive(True)
        self.view_theme_dark_action = QAction("Dark", self)
        self.view_theme_dark_action.setCheckable(True)
        self.view_theme_dark_action.setActionGroup(self.view_theme_group)
        self.view_theme_dark_action.triggered.connect(
            lambda checked=False: self._apply_theme_mode("dark")
        )
        self.view_theme_menu.addAction(self.view_theme_dark_action)

        self.view_theme_light_action = QAction("Light", self)
        self.view_theme_light_action.setCheckable(True)
        self.view_theme_light_action.setActionGroup(self.view_theme_group)
        self.view_theme_light_action.triggered.connect(
            lambda checked=False: self._apply_theme_mode("light")
        )
        self.view_theme_menu.addAction(self.view_theme_light_action)

        self.view_experiments_menu = view_menu.addMenu("Experiments")
        self.view_files_experiment_action = QAction("Files UI v1", self)
        self.view_files_experiment_action.setCheckable(True)
        self.view_files_experiment_action.setChecked(self._is_files_experiment_enabled())
        self.view_files_experiment_action.toggled.connect(self._set_files_experiment_enabled)
        self.view_experiments_menu.addAction(self.view_files_experiment_action)

        self.view_reset_layout_action = QAction("Reset Layout", self)
        self.view_reset_layout_action.triggered.connect(self._reset_layout)
        view_menu.addAction(self.view_reset_layout_action)

        view_menu.addSeparator()
        self._build_ui_scale_menu(view_menu, title="Zoom")

        printer_menu = menu_bar.addMenu("Printer")
        self.printer_connect_action = QAction("Connection Window...", self)
        self.printer_connect_action.setShortcut("Ctrl+Shift+C")
        self.printer_connect_action.triggered.connect(self._connect_ssh_from_command_bar)
        printer_menu.addAction(self.printer_connect_action)
        self.tools_connect_menu = printer_menu.addMenu("Connect")
        self.tools_connect_menu.aboutToShow.connect(self._refresh_tools_connect_menu)
        self._refresh_tools_connect_menu()

        self.printer_disconnect_action = QAction("Disconnect", self)
        self.printer_disconnect_action.triggered.connect(self._disconnect_printer)
        printer_menu.addAction(self.printer_disconnect_action)

        self.printer_manage_saved_action = QAction("Manage Saved Connections", self)
        self.printer_manage_saved_action.triggered.connect(self._open_saved_connections_manager)
        printer_menu.addAction(self.printer_manage_saved_action)

        self.printer_open_control_action = QAction("Open Control UI (Mainsail/Fluidd)", self)
        self.printer_open_control_action.triggered.connect(self._manage_open_control_window)
        printer_menu.addAction(self.printer_open_control_action)

        self.printer_upload_action = QAction("Upload Current", self)
        self.printer_upload_action.setShortcut("Ctrl+Shift+U")
        self.printer_upload_action.triggered.connect(self._upload_current_context)
        printer_menu.addAction(self.printer_upload_action)

        self.printer_restart_klipper_action = QAction("Restart Klipper", self)
        self.printer_restart_klipper_action.setShortcut("Ctrl+Shift+R")
        self.printer_restart_klipper_action.triggered.connect(self._restart_current_context)
        printer_menu.addAction(self.printer_restart_klipper_action)

        self.printer_restart_host_action = QAction("Restart Host", self)
        self.printer_restart_host_action.triggered.connect(self._restart_host_service)
        printer_menu.addAction(self.printer_restart_host_action)

        configuration_menu = menu_bar.addMenu("Configuration")
        self.tools_open_remote_action = QAction("Open Remote Config", self)
        self.tools_open_remote_action.triggered.connect(self._fetch_remote_cfg_file)
        configuration_menu.addAction(self.tools_open_remote_action)

        self.configuration_open_local_action = QAction("Open Local Config", self)
        self.configuration_open_local_action.triggered.connect(self._open_local_cfg_file)
        configuration_menu.addAction(self.configuration_open_local_action)

        self.configuration_validate_action = QAction("Validate Current", self)
        self.configuration_validate_action.setShortcut("Ctrl+Shift+V")
        self.configuration_validate_action.triggered.connect(self._validate_current_context)
        configuration_menu.addAction(self.configuration_validate_action)

        self.configuration_refactor_action = QAction("Refactor Current", self)
        self.configuration_refactor_action.triggered.connect(self._refactor_current_cfg_file)
        configuration_menu.addAction(self.configuration_refactor_action)

        self.configuration_apply_form_action = QAction("Apply Form Changes", self)
        self.configuration_apply_form_action.triggered.connect(self._apply_cfg_form_changes)
        configuration_menu.addAction(self.configuration_apply_form_action)

        self.configuration_compile_action = QAction("Compile / Generate", self)
        self.configuration_compile_action.setShortcut("Ctrl+Shift+G")
        self.configuration_compile_action.triggered.connect(self._render_and_validate)
        configuration_menu.addAction(self.configuration_compile_action)

        self.configuration_section_overrides_action = QAction("Section Overrides", self)
        self.configuration_section_overrides_action.triggered.connect(
            self._open_section_overrides
        )
        configuration_menu.addAction(self.configuration_section_overrides_action)

        tools_menu = menu_bar.addMenu("Tools")
        self.tools_printer_discovery_action = QAction("Scan For Printers...", self)
        self.tools_printer_discovery_action.triggered.connect(self._open_printer_discovery)
        tools_menu.addAction(self.tools_printer_discovery_action)
        self.tools_scan_printers_action = self.tools_printer_discovery_action

        self.tools_active_console_action = QAction("Active Console", self)
        self.tools_active_console_action.triggered.connect(self._open_active_console_window)
        tools_menu.addAction(self.tools_active_console_action)

        self.tools_explore_config_action = QAction("Explore Config Directory", self)
        self.tools_explore_config_action.triggered.connect(
            self._explore_connected_config_directory
        )
        tools_menu.addAction(self.tools_explore_config_action)

        self.tools_backup_manager_action = QAction("Backup Manager", self)
        self.tools_backup_manager_action.triggered.connect(self._open_backup_manager)
        tools_menu.addAction(self.tools_backup_manager_action)

        self.tools_firmware_info_action = QAction("Firmware Info", self)
        self.tools_firmware_info_action.triggered.connect(self._show_firmware_info)
        tools_menu.addAction(self.tools_firmware_info_action)

        self.import_existing_machine_action = QAction("Import Machine Analysis", self)
        self.import_existing_machine_action.triggered.connect(
            self._import_existing_machine_entrypoint
        )
        tools_menu.addAction(self.import_existing_machine_action)

        self.tools_advanced_settings_menu = tools_menu.addMenu("Advanced Settings")
        self.tools_guided_component_setup_action = QAction("Guided Component Setup...", self)
        self.tools_guided_component_setup_action.triggered.connect(
            self._open_guided_component_setup
        )
        self.tools_advanced_settings_menu.addAction(self.tools_guided_component_setup_action)

        self.tools_deploy_action = QAction("Deploy Generated Pack", self)
        self.tools_deploy_action.triggered.connect(self._deploy_generated_pack)
        self.tools_advanced_settings_menu.addAction(self.tools_deploy_action)

        self.tools_advanced_settings_action = QAction("Open Settings Dialog", self)
        self.tools_advanced_settings_action.triggered.connect(self._open_settings_dialog)
        self.tools_advanced_settings_menu.addAction(self.tools_advanced_settings_action)

        help_menu = menu_bar.addMenu("Help")
        self.help_docs_action = QAction("Documentation", self)
        self.help_docs_action.triggered.connect(self._open_documentation)
        help_menu.addAction(self.help_docs_action)

        self.help_quick_start_action = QAction("Quick Start", self)
        self.help_quick_start_action.triggered.connect(self._show_quick_start)
        help_menu.addAction(self.help_quick_start_action)

        self.help_shortcuts_action = QAction("Keyboard Shortcuts", self)
        self.help_shortcuts_action.triggered.connect(self._show_keyboard_shortcuts)
        help_menu.addAction(self.help_shortcuts_action)

        self.help_about_action = QAction("About", self)
        self.help_about_action.triggered.connect(self._show_about_window)
        help_menu.addAction(self.help_about_action)

        self._apply_theme_mode(self.theme_mode, persist=False)

    def _build_ui_scale_menu(self, view_menu, *, title: str = "UI Scale") -> None:
        scale_menu = view_menu.addMenu(title)
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

    def _save_project(self) -> None:
        if self.current_project_path:
            self._save_project_to_path(self.current_project_path)
            return
        self._save_project_to_file()

    def _save_project_to_path(self, path: str) -> None:
        try:
            project = self._build_project_from_ui()
        except (ValidationError, ValueError) as exc:
            self._show_error("Save Failed", str(exc))
            return

        try:
            self.project_store.save(path, project)
        except OSError as exc:
            self._show_error("Save Failed", str(exc))
            return

        self.current_project_path = str(path)
        self.statusBar().showMessage(f"Saved project: {path}", 2500)

    def _import_preset_placeholder(self) -> None:
        QMessageBox.information(
            self,
            "Import Preset",
            (
                "Preset import files are not supported in this build.\n\n"
                "Use bundled presets from Configuration, or use Tools -> Import Machine Analysis "
                "to learn settings from an existing machine."
            ),
        )

    def _export_generated_pack_dialog(self) -> None:
        if not self._ensure_export_ready():
            return

        dialog = QMessageBox(self)
        dialog.setWindowTitle("Export Generated Pack")
        dialog.setText("Choose export format.")
        folder_button = dialog.addButton("Folder", QMessageBox.ButtonRole.AcceptRole)
        zip_button = dialog.addButton("ZIP", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()

        clicked = dialog.clickedButton()
        if clicked is folder_button:
            self._export_folder()
            return
        if clicked is zip_button:
            self._export_zip()

    def _open_settings_dialog(self) -> None:
        QMessageBox.information(
            self,
            "Settings",
            (
                "Use these command-bar settings areas:\n"
                "- View -> Theme (Dark/Light)\n"
                "- View -> Zoom\n"
                "- Tools -> Advanced Settings"
            ),
        )

    def _set_sidebar_visible(self, visible: bool) -> None:
        self.app_state_store.update_ui(left_nav_visible=visible)
        if hasattr(self, "route_nav_bar"):
            self.route_nav_bar.setVisible(visible)

        self.statusBar().showMessage(
            "Navigation bar shown" if visible else "Navigation bar hidden",
            2000,
        )

    def _set_console_visible(self, visible: bool) -> None:
        self.app_state_store.update_ui(right_panel_mode="logs" if visible else "context")
        console_window = self._ensure_active_console_window()
        if visible:
            console_window.show()
            console_window.raise_()
            console_window.activateWindow()
        else:
            console_window.hide()
        self.statusBar().showMessage(
            "Console shown" if visible else "Console hidden",
            2000,
        )

    def _set_advanced_mode(self, enabled: bool) -> None:
        if hasattr(self, "tabs"):
            self.tabs.tabBar().setVisible(enabled)
        if enabled:
            self.app_state_store.update_ui(legacy_visible=True)
            self.statusBar().showMessage("Advanced mode enabled", 2000)
            return
        self.app_state_store.update_ui(legacy_visible=False)
        self.statusBar().showMessage("Advanced mode hidden", 2000)

    def _connect_ssh_from_command_bar(self) -> None:
        self._open_printer_connection_window()

    def _active_route(self) -> str:
        snapshot = self.app_state_store.snapshot()
        return (snapshot.ui.active_route or "home").strip().lower()

    def _has_ssh_target_configured(self) -> bool:
        if not hasattr(self, "ssh_host_edit") or not hasattr(self, "ssh_username_edit"):
            return False
        return bool(self.ssh_host_edit.text().strip()) and bool(self.ssh_username_edit.text().strip())

    def _has_modify_cfg_context(self) -> bool:
        if not hasattr(self, "modify_editor") or not hasattr(self, "modify_remote_cfg_path_edit"):
            return False
        remote_path = (self.modify_current_remote_file or "").strip()
        if not remote_path:
            remote_path = self.modify_remote_cfg_path_edit.text().strip()
        return bool(remote_path.lower().endswith(".cfg")) and bool(
            self.modify_editor.toPlainText().strip()
        )

    def _has_manage_cfg_context(self) -> bool:
        if not hasattr(self, "manage_file_editor"):
            return False
        remote_path = (self.manage_current_remote_file or "").strip()
        return bool(remote_path.lower().endswith(".cfg")) and bool(
            self.manage_file_editor.toPlainText().strip()
        )

    def _has_files_cfg_context(self) -> bool:
        return self._current_cfg_context(show_error=False) is not None

    def _can_upload_generated_pack(self) -> bool:
        return (
            self.current_project is not None
            and self.current_pack is not None
            and not self.current_report.has_blocking
            and self._has_ssh_target_configured()
        )

    def _can_validate_current_context(self) -> bool:
        route = self._active_route()
        if route == "edit_config":
            return self._has_modify_cfg_context()
        if route == "backups":
            return self._has_manage_cfg_context()
        return self._has_files_cfg_context()

    def _can_upload_current_context(self) -> bool:
        route = self._active_route()
        if route == "edit_config":
            return self.device_connected and self._has_modify_cfg_context()
        if route == "backups":
            return self.device_connected and bool((self.manage_current_remote_file or "").strip())
        return self._can_upload_generated_pack()

    def _validate_current_context(self, _checked: bool = False) -> None:
        route = self._active_route()
        if route == "edit_config":
            self._modify_validate_current_file()
            return
        if route == "backups":
            self._manage_validate_current_file()
            return
        self._validate_current_cfg_file()

    def _upload_current_context(self, _checked: bool = False) -> None:
        route = self._active_route()
        if route == "edit_config":
            self._modify_upload_current_file()
            return
        if route == "backups":
            self._manage_save_current_file()
            return
        self._deploy_generated_pack()

    def _restart_current_context(self, _checked: bool = False) -> None:
        if self._active_route() == "edit_config":
            self._modify_test_restart()
            return
        self._restart_klipper_service()

    def _toggle_raw_form_mode(self) -> None:
        if not hasattr(self, "file_view_tabs"):
            return
        next_index = 1 if self.file_view_tabs.currentIndex() == 0 else 0
        self.file_view_tabs.setCurrentIndex(next_index)
        label = "Form" if next_index == 1 else "Raw"
        self.action_log_service.log_event("files_view_mode", mode=label.lower())
        self.statusBar().showMessage(f"Files view set to {label} mode", 2000)

    def _apply_theme_mode(self, mode: str, *, persist: bool = True) -> None:
        selected = mode.strip().lower()
        if selected not in {"dark", "light"}:
            selected = "dark"

        app = QApplication.instance()
        if app is None:
            return

        stylesheet = build_base_stylesheet(selected)
        if self._is_files_experiment_enabled():
            stylesheet += "\n" + build_files_material_stylesheet(selected)
        app.setStyleSheet(stylesheet)
        self.theme_mode = selected
        if hasattr(self, "view_theme_dark_action"):
            self.view_theme_dark_action.setChecked(selected == "dark")
        if hasattr(self, "view_theme_light_action"):
            self.view_theme_light_action.setChecked(selected == "light")
        if persist:
            self.app_settings.setValue("ui/theme_mode", selected)
            self.app_settings.sync()
        self.statusBar().showMessage(f"Theme set to {selected.title()}", 2000)

    def _reset_layout(self) -> None:
        if hasattr(self, "view_toggle_sidebar_action"):
            self.view_toggle_sidebar_action.setChecked(True)
        else:
            self._set_sidebar_visible(True)

        self._set_console_visible(False)

        if hasattr(self, "file_view_tabs"):
            self.file_view_tabs.setCurrentIndex(0)
        if hasattr(self, "wizard_package_splitter"):
            self.wizard_package_splitter.setSizes([1, 3])
        if hasattr(self, "files_splitter"):
            self.files_splitter.setSizes([1, 3])
        self.statusBar().showMessage("Layout reset", 2500)

    def _open_manage_addons(self) -> None:
        QMessageBox.information(
            self,
            "Add-ons Disabled",
            (
                "Add-on support is temporarily disabled in this build because it is unreliable.\n"
                "It will return in a future release after targeted rework."
            ),
        )

    def _open_section_overrides(self) -> None:
        self.tabs.setCurrentWidget(self.files_tab)
        self.app_state_store.update_ui(active_route="edit_config", right_panel_mode="validation")
        if hasattr(self, "overrides_section_toggle"):
            self.overrides_section_toggle.setChecked(True)
        self.statusBar().showMessage("Opened section overrides", 2500)

    def _open_printer_discovery(self) -> None:
        discovery_window = self._ensure_printer_discovery_window()
        discovery_window.show()
        discovery_window.raise_()
        discovery_window.activateWindow()
        self._scan_for_printers()

    def _open_backup_manager(self) -> None:
        self.tabs.setCurrentWidget(self.manage_printer_tab)
        self.app_state_store.update_ui(active_route="backups", right_panel_mode="context")
        self._manage_refresh_backups()
        self.statusBar().showMessage("Opened backup manager", 2500)

    def _show_firmware_info(self) -> None:
        project = self.current_project
        pack = self.current_pack
        if project is None:
            QMessageBox.information(self, "Firmware Info", "No active project loaded.")
            return
        preset_name = self.current_preset.name if self.current_preset is not None else project.preset_id
        file_count = len(pack.files) if pack is not None else 0
        QMessageBox.information(
            self,
            "Firmware Info",
            (
                f"Preset: {preset_name}\n"
                f"Mainboard: {project.board}\n"
                f"Build volume: {project.dimensions.x} x {project.dimensions.y} x {project.dimensions.z}\n"
                f"Generated files: {file_count}\n"
                f"Validation blocking issues: {sum(1 for f in self.current_report.findings if f.severity == 'blocking')}"
            ),
        )

    def _open_saved_connections_manager(self) -> None:
        self._open_printer_connection_window()
        self._refresh_saved_connection_profiles()
        self.statusBar().showMessage("Manage saved connections in Printer Connection window", 2500)

    def _disconnect_printer(self) -> None:
        self._set_device_connection_health(False, "Disconnected from printer.")
        self.preview_connected_printer_name = None
        self.preview_connected_host = None
        self._set_manage_connected_printer_display(None, None, connected=False)
        self._set_modify_connected_printer_display(None, None, connected=False)
        self._append_ssh_log("Disconnected printer session.")
        self._append_modify_log("Disconnected printer session.")
        self._append_manage_log("Disconnected printer session.")
        self.statusBar().showMessage("Disconnected", 2500)

    def _run_printer_command(
        self,
        *,
        command: str,
        action_name: str,
        disconnect_after: bool = False,
    ) -> None:
        if not self.device_connected:
            self._show_error("Printer Command", "Connect to a printer first.")
            return

        service = self._get_ssh_service()
        if service is None:
            return
        params = self._collect_ssh_params()
        if params is None:
            return

        self.action_log_service.log_event(
            "restart",
            phase="start",
            action_name=action_name,
            command=command,
            host=str(params["host"]),
        )
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            output = service.run_remote_command(command=command, **params).strip()
        except SSHDeployError as exc:
            self.app_state_store.update_deploy(last_restart_status=f"failed: {exc}")
            self._set_device_connection_health(False, str(exc))
            self._show_error(action_name, str(exc))
            self.action_log_service.log_event(
                "restart",
                phase="failed",
                action_name=action_name,
                command=command,
                host=str(params["host"]),
                error=str(exc),
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        summary = output or "(no output)"
        self._append_ssh_log(f"{action_name}: {summary}")
        self._append_modify_log(f"{action_name}: {summary}")
        self._append_manage_log(f"{action_name}: {summary}")
        self.app_state_store.update_deploy(last_restart_status=summary)
        self.action_log_service.log_event(
            "restart",
            phase="complete",
            action_name=action_name,
            command=command,
            host=str(params["host"]),
            output=summary,
        )
        if disconnect_after:
            self._disconnect_printer()
            self.statusBar().showMessage(f"{action_name} issued: {summary}", 3000)
            return
        self._set_device_connection_health(True, f"{action_name} succeeded.")
        self.statusBar().showMessage(f"{action_name} succeeded", 3000)

    def _restart_klipper_service(self) -> None:
        command = self.ssh_restart_cmd_edit.text().strip() or "sudo systemctl restart klipper"
        self._run_printer_command(command=command, action_name="Restart Klipper")

    def _restart_host_service(self) -> None:
        answer = QMessageBox.question(
            self,
            "Restart Host",
            "Issue 'sudo reboot' on the connected printer host?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_printer_command(
            command="sudo reboot",
            action_name="Restart Host",
            disconnect_after=True,
        )

    def _view_printer_console_log(self) -> None:
        self._open_active_console_window()

    def _open_documentation(self) -> None:
        readme = Path(__file__).resolve().parents[2] / "README.md"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(readme)))

    def _show_quick_start(self) -> None:
        QMessageBox.information(
            self,
            "Quick Start",
            (
                "1. Select Vendor, Preset, and Mainboard in Configuration.\n"
                "2. Fill hardware fields (probe/toolhead/thermistors).\n"
                "3. Use Configuration -> Compile / Generate.\n"
                "4. Review files in Files tab.\n"
                "5. Export via File -> Export Generated Pack..."
            ),
        )

    def _show_keyboard_shortcuts(self) -> None:
        QMessageBox.information(
            self,
            "Keyboard Shortcuts",
            (
                "Common shortcuts:\n"
                "Ctrl+S: Save machine profile project\n"
                "Ctrl+Shift+S: Save project as\n"
                "Ctrl+O: Open machine profile\n"
                "Ctrl+Shift+C: Connect\n"
                "Ctrl+Shift+V: Validate Current\n"
                "Ctrl+Shift+G: Compile / Generate\n"
                "Ctrl+Shift+U: Upload Current\n"
                "Ctrl+Shift+R: Restart Klipper\n"
                "Ctrl+Q: Exit"
            ),
        )

    def _go_to_configuration_tab(self) -> None:
        self.tabs.setCurrentWidget(self.wizard_tab)

    def _go_to_modify_existing_tab(self) -> None:
        self.tabs.setCurrentWidget(self.modify_existing_tab)

    def _go_to_ssh_tab(self) -> None:
        self._open_printer_connection_window()

    def _guided_prompt_text(
        self,
        title: str,
        prompt: str,
        default_value: str = "",
    ) -> tuple[bool, str]:
        value, ok = QInputDialog.getText(self, title, prompt, text=default_value)
        return ok, value

    def _guided_prompt_choice(
        self,
        title: str,
        prompt: str,
        options: list[str],
        *,
        default_index: int = 0,
    ) -> tuple[bool, str]:
        if not options:
            return False, ""
        if default_index < 0 or default_index >= len(options):
            default_index = 0
        selected, ok = QInputDialog.getItem(
            self,
            title,
            prompt,
            options,
            default_index,
            False,
        )
        return ok, selected

    @staticmethod
    def _slugify_component_id(raw_value: str) -> str:
        value = raw_value.strip().lower()
        value = re.sub(r"[^a-z0-9_]+", "_", value)
        value = re.sub(r"_+", "_", value)
        return value.strip("_")

    def _choose_bundle_target_root(self) -> Path | None:
        user_root = default_user_bundles_dir()
        builtin_root = default_bundles_dir()
        options = [
            f"User bundles ({user_root}) [Recommended]",
            f"Built-in bundles ({builtin_root})",
            "Custom folder...",
        ]
        ok, choice = self._guided_prompt_choice(
            "Guided Component Setup",
            "Where should new component bundles be created?",
            options,
            default_index=0,
        )
        if not ok:
            return None
        if choice.startswith("User bundles"):
            return user_root
        if choice.startswith("Built-in bundles"):
            return builtin_root

        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose Bundle Root Directory",
            str(Path.home()),
        )
        if not folder:
            return None
        return Path(folder).expanduser()

    def _collect_mainboard_bundle_spec(self) -> dict[str, Any] | None:
        title = "Mainboard Bundle Wizard"
        ok, raw_id = self._guided_prompt_text(title, "Board ID", "my_custom_mainboard")
        if not ok:
            return None
        board_id = self._slugify_component_id(raw_id) or "my_custom_mainboard"

        ok, label = self._guided_prompt_text(title, "Board label", "My Custom Mainboard")
        if not ok:
            return None
        ok, mcu = self._guided_prompt_text(title, "MCU", "stm32f446xx")
        if not ok:
            return None
        serial_default = f"/dev/serial/by-id/usb-{board_id}"
        ok, serial_hint = self._guided_prompt_text(title, "Serial hint", serial_default)
        if not ok:
            return None

        payload = {
            "id": board_id,
            "label": label.strip() or board_id,
            "mcu": mcu.strip() or "stm32f446xx",
            "serial_hint": serial_hint.strip() or serial_default,
            "pins": {
                "stepper_x_step": "PB13",
                "stepper_x_dir": "PB12",
                "stepper_x_enable": "PB14",
                "heater_hotend": "PA2",
                "temp_hotend": "PF4",
            },
            "layout": {
                "Stepper Drivers": ["X", "Y", "Z", "E0"],
                "Heaters": ["HE0", "BED"],
            },
        }
        return {"component_type": "mainboard", "id": board_id, "payload": payload}

    def _collect_toolhead_bundle_spec(self) -> dict[str, Any] | None:
        title = "Toolhead PCB Bundle Wizard"
        ok, raw_id = self._guided_prompt_text(title, "Toolhead board ID", "my_custom_toolhead")
        if not ok:
            return None
        board_id = self._slugify_component_id(raw_id) or "my_custom_toolhead"

        ok, label = self._guided_prompt_text(title, "Toolhead label", "My Custom Toolhead")
        if not ok:
            return None
        ok, mcu = self._guided_prompt_text(title, "MCU", "rp2040")
        if not ok:
            return None
        ok, transport = self._guided_prompt_choice(
            title,
            "Transport type",
            ["can", "usb"],
            default_index=0,
        )
        if not ok:
            return None
        serial_default = (
            "canbus_uuid: replace-with-uuid"
            if transport == "can"
            else f"/dev/serial/by-id/usb-{board_id}"
        )
        ok, serial_hint = self._guided_prompt_text(title, "Serial hint", serial_default)
        if not ok:
            return None

        payload = {
            "id": board_id,
            "label": label.strip() or board_id,
            "mcu": mcu.strip() or "rp2040",
            "transport": transport,
            "serial_hint": serial_hint.strip() or serial_default,
            "pins": {
                "extruder_step": "toolhead:EXT_STEP",
                "extruder_dir": "toolhead:EXT_DIR",
                "extruder_enable": "toolhead:EXT_EN",
                "heater_hotend": "toolhead:HE0",
                "temp_hotend": "toolhead:TH0",
            },
            "layout": {
                "Motor and Heater": ["EXT_STEP", "EXT_DIR", "EXT_EN", "HE0"],
                "Sensors": ["TH0", "PROBE", "FS0"],
            },
        }
        return {"component_type": "toolhead_board", "id": board_id, "payload": payload}

    def _collect_addon_bundle_spec(self) -> dict[str, Any] | None:
        title = "Add-on Bundle Wizard"
        ok, raw_id = self._guided_prompt_text(title, "Add-on ID", "my_custom_addon")
        if not ok:
            return None
        addon_id = self._slugify_component_id(raw_id) or "my_custom_addon"

        ok, label = self._guided_prompt_text(title, "Add-on label", "My Custom Add-on")
        if not ok:
            return None
        ok, description = self._guided_prompt_text(
            title,
            "Description",
            "Add-on scaffold generated by Guided Component Setup.",
        )
        if not ok:
            return None
        template_default = f"addons/{addon_id}.cfg.j2"
        ok, template_rel = self._guided_prompt_text(
            title,
            "Template path (relative to templates/)",
            template_default,
        )
        if not ok:
            return None
        template_rel = template_rel.strip().replace("\\", "/") or template_default

        ok, family_text = self._guided_prompt_text(
            title,
            "Supported families (comma separated)",
            "voron",
        )
        if not ok:
            return None
        supported_families = [
            family.strip() for family in family_text.split(",") if family.strip()
        ] or ["voron"]

        create_template_choice = QMessageBox.question(
            self,
            title,
            f"Create template scaffold at templates/{template_rel}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        create_template = create_template_choice == QMessageBox.StandardButton.Yes
        template_content = ""
        if create_template:
            macro_name = self._slugify_component_id(addon_id).upper() or "CUSTOM_ADDON"
            template_content = (
                f"# Add-on scaffold: {label.strip() or addon_id}\n"
                f"[gcode_macro {macro_name}_STATUS]\n"
                "gcode:\n"
                '  RESPOND PREFIX="info" MSG="Addon ready"\n'
            )

        payload = {
            "id": addon_id,
            "label": label.strip() or addon_id,
            "template": template_rel,
            "description": description.strip(),
            "multi_material": False,
            "recommends_toolhead": False,
            "supported_families": supported_families,
        }
        spec: dict[str, Any] = {"component_type": "addon", "id": addon_id, "payload": payload}
        if create_template:
            spec["template_rel"] = template_rel
            spec["template_content"] = template_content
        return spec

    def _collect_macro_template_spec(self) -> dict[str, Any] | None:
        title = "Macro Template Wizard"
        ok, raw_id = self._guided_prompt_text(title, "Macro template ID", "my_custom_macro")
        if not ok:
            return None
        macro_id = self._slugify_component_id(raw_id) or "my_custom_macro"
        template_rel = f"macros/{macro_id}.cfg.j2"
        macro_name = macro_id.upper()
        template_content = (
            f"# Macro scaffold: {macro_id}\n"
            f"[gcode_macro {macro_name}]\n"
            "gcode:\n"
            "  RESPOND PREFIX=\"info\" MSG=\"Custom macro pack scaffold\"\n"
        )
        return {
            "component_type": "macro_template",
            "id": macro_id,
            "template_rel": template_rel,
            "template_content": template_content,
        }

    def _run_guided_component_setup_wizard(self) -> tuple[Path, dict[str, Any]] | None:
        ok, component_type = self._guided_prompt_choice(
            "Guided Component Setup",
            "What component would you like to create?",
            [
                "Mainboard Bundle",
                "Toolhead PCB Bundle",
                "Macro Template Scaffold",
            ],
            default_index=0,
        )
        if not ok:
            return None

        bundle_root = self._choose_bundle_target_root()
        if bundle_root is None:
            return None

        builders: dict[str, Any] = {
            "Mainboard Bundle": self._collect_mainboard_bundle_spec,
            "Toolhead PCB Bundle": self._collect_toolhead_bundle_spec,
            "Macro Template Scaffold": self._collect_macro_template_spec,
        }
        builder = builders.get(component_type)
        if builder is None:
            return None
        spec = builder()
        if spec is None:
            return None
        return bundle_root, spec

    def _write_guided_bundle_spec(self, bundle_root: Path, spec: dict[str, Any]) -> list[Path]:
        created: list[Path] = []
        component_type = str(spec.get("component_type") or "")
        component_id = str(spec.get("id") or "").strip()

        if component_type == "addon":
            raise ValueError("Add-on bundle creation is disabled in this build.")

        if component_type in {"mainboard", "toolhead_board"}:
            if not component_id:
                raise ValueError("Component ID is required.")
            subdir = {
                "mainboard": "boards",
                "toolhead_board": "toolhead_boards",
            }[component_type]
            payload = spec.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("Bundle payload is missing.")
            target_json = bundle_root / subdir / f"{component_id}.json"
            self._write_guided_file(
                target_json,
                json.dumps(payload, indent=2, sort_keys=False) + "\n",
            )
            created.append(target_json)

        template_rel = str(spec.get("template_rel") or "").strip().replace("\\", "/")
        if template_rel:
            template_parts = [part for part in template_rel.split("/") if part]
            if any(part == ".." for part in template_parts):
                raise ValueError("Template path cannot contain '..'.")
            template_path = bundle_root / "templates" / Path(*template_parts)
            template_content = str(spec.get("template_content") or "").rstrip() + "\n"
            self._write_guided_file(template_path, template_content)
            created.append(template_path)

        if not created:
            raise ValueError("No output was generated by guided component setup.")
        return created

    def _write_guided_file(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            answer = QMessageBox.question(
                self,
                "Overwrite Existing File",
                f"File already exists:\n{path}\n\nOverwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                raise RuntimeError("User cancelled overwrite.")
        path.write_text(content, encoding="utf-8")

    def _refresh_bundle_backed_component_options(self) -> None:
        refresh_bundle_catalog()
        if self.current_preset is None:
            return
        available_boards = sorted(set(self.current_preset.supported_boards).union(list_main_boards()))
        available_toolheads = sorted(
            set(self.current_preset.supported_toolhead_boards).union(list_toolhead_boards())
        )
        self._populate_board_combo(available_boards)
        self._populate_toolhead_board_combos(available_toolheads)
        self._refresh_board_summary()
        self._render_and_validate()

    def _open_guided_component_setup(self) -> None:
        guided_result = self._run_guided_component_setup_wizard()
        if guided_result is None:
            return
        bundle_root, spec = guided_result

        try:
            created_paths = self._write_guided_bundle_spec(bundle_root, spec)
        except RuntimeError:
            return
        except (OSError, ValueError) as exc:
            self._show_error("Guided Component Setup", str(exc))
            return

        self._refresh_bundle_backed_component_options()
        created_lines = "\n".join(str(path) for path in created_paths)
        QMessageBox.information(
            self,
            "Guided Component Setup",
            (
                "Created bundle files:\n"
                f"{created_lines}\n\n"
                "Reloaded bundle catalog. New boards/toolhead boards are available immediately."
            ),
        )
        self.statusBar().showMessage("Guided component setup created bundle files", 3000)

    def _learn_addons_from_import(self) -> None:
        QMessageBox.information(
            self,
            "Add-ons Disabled",
            (
                "Add-on support is temporarily disabled in this build because it is unreliable.\n"
                "Learning and bundle generation for add-ons are disabled for now."
            ),
        )

    def _show_about_window(self) -> None:
        self._ensure_about_window()
        if self.about_window is None:
            return
        self.about_window.show()
        self.about_window.raise_()
        self.about_window.activateWindow()

    def _ensure_about_window(self) -> None:
        if self.about_window is not None:
            return
        about_window = QMainWindow(self)
        about_window.setWindowTitle("About KlippConfig")
        about_window.resize(760, 700)
        about_window.setCentralWidget(self._build_about_view(about_window))
        self.about_window = about_window

    def _choose_import_source(self) -> tuple[str | None, str | None]:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Import Existing Machine")
        dialog.setText("Choose an existing configuration source.")
        zip_button = dialog.addButton("ZIP File", QMessageBox.ButtonRole.AcceptRole)
        folder_button = dialog.addButton("Folder", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()

        clicked = dialog.clickedButton()
        if clicked is zip_button:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Import Existing Machine ZIP",
                str(Path.home()),
                "ZIP files (*.zip)",
            )
            if path:
                return path, "zip"
            return None, None
        if clicked is folder_button:
            path = QFileDialog.getExistingDirectory(
                self,
                "Import Existing Machine Folder",
                str(Path.home()),
            )
            if path:
                return path, "folder"
        return None, None

    def _import_existing_machine_entrypoint(self) -> None:
        path, source_kind = self._choose_import_source()
        if not path or not source_kind:
            return
        self._import_existing_machine_from_path(path, source_kind)

    def _import_existing_machine_from_path(self, path: str, source_kind: str) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if source_kind == "zip":
                profile = self.existing_machine_import_service.import_zip(path)
            else:
                profile = self.existing_machine_import_service.import_folder(path)
        except ExistingMachineImportError as exc:
            self._show_error("Import Existing Machine", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        file_map = profile.detected.get("file_map")
        if not isinstance(file_map, dict):
            file_map = dict(self.existing_machine_import_service.last_import_files)
        normalized_file_map = {
            str(file_path): str(content)
            for file_path, content in file_map.items()
            if str(file_path).strip()
        }
        if not normalized_file_map:
            self._show_error("Import Existing Machine", "No readable files found in import source.")
            return

        self._load_imported_machine_profile(profile, normalized_file_map)
        self.tabs.setCurrentWidget(self.files_tab)
        self.statusBar().showMessage(f"Imported existing machine: {Path(path).name}", 3000)

    def _load_imported_machine_profile(
        self,
        profile: ImportedMachineProfile,
        file_map: dict[str, str],
    ) -> None:
        self.current_import_profile = profile
        self.imported_file_map = dict(file_map)
        ordered = sorted(self.imported_file_map.keys(), key=str.casefold)
        root_file = profile.root_file
        if root_file in ordered:
            ordered.remove(root_file)
            ordered.insert(0, root_file)
        self.imported_file_order = ordered
        self._showing_external_file = True

        self.generated_file_list.blockSignals(True)
        self.generated_file_list.clear()
        for file_path in ordered:
            self.generated_file_list.addItem(file_path)
        self.generated_file_list.blockSignals(False)

        if ordered:
            self.generated_file_list.setCurrentRow(0)
            self._show_selected_imported_file()
        else:
            self._set_files_tab_content(
                content="",
                label="No imported files.",
                source="imported",
                generated_name=None,
            )

        if hasattr(self, "machine_profile_name_edit"):
            self.machine_profile_name_edit.setText(profile.name)
        self._populate_import_review(profile)

    @staticmethod
    def _format_import_value(value: Any) -> str:
        if isinstance(value, list):
            if len(value) > 6:
                return f"{len(value)} item(s)"
            return ", ".join(str(item) for item in value)
        if isinstance(value, dict):
            if len(value) > 6:
                return f"{len(value)} key(s)"
            return ", ".join(f"{k}={v}" for k, v in value.items())
        return str(value)

    def _populate_import_review(self, profile: ImportedMachineProfile) -> None:
        self.import_review_suggestions = list(profile.suggestions)
        self.import_review_suggestions = [
            suggestion
            for suggestion in self.import_review_suggestions
            if suggestion.field not in self.ADDON_IMPORT_FIELDS
        ]
        self.import_review_table.setRowCount(len(self.import_review_suggestions))

        for row, suggestion in enumerate(self.import_review_suggestions):
            apply_item = QTableWidgetItem("")
            apply_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            apply_item.setCheckState(
                Qt.CheckState.Checked if suggestion.auto_apply else Qt.CheckState.Unchecked
            )
            self.import_review_table.setItem(row, 0, apply_item)
            self.import_review_table.setItem(row, 1, QTableWidgetItem(suggestion.field))
            self.import_review_table.setItem(row, 2, QTableWidgetItem(self._format_import_value(suggestion.value)))
            self.import_review_table.setItem(
                row,
                3,
                QTableWidgetItem(f"{suggestion.confidence * 100:.0f}%"),
            )
            self.import_review_table.setItem(row, 4, QTableWidgetItem(suggestion.reason))
            self.import_review_table.setItem(row, 5, QTableWidgetItem(suggestion.source_file))

        warning_count = len(profile.analysis_warnings)
        suggestion_count = len(self.import_review_suggestions)
        unmapped_count = sum(len(items) for items in profile.unmapped_sections.values())
        if warning_count > 0:
            preview_warning = profile.analysis_warnings[0]
            self.import_review_status_label.setText(
                f"Loaded {suggestion_count} suggestion(s). {warning_count} warning(s). "
                f"Example: {preview_warning}"
            )
            self.import_review_status_label.setStyleSheet(
                "QLabel { background-color: #78350f; color: #ffffff; border: 1px solid #f59e0b; "
                "border-radius: 4px; padding: 6px 8px; font-weight: 600; }"
            )
        else:
            self.import_review_status_label.setText(
                f"Loaded {suggestion_count} suggestion(s). "
                f"Unmapped sections: {unmapped_count}. Select rows to apply."
            )
            self.import_review_status_label.setStyleSheet(
                "QLabel { background-color: #111827; color: #e5e7eb; border: 1px solid #374151; "
                "border-radius: 4px; padding: 6px 8px; }"
            )

    def _select_high_confidence_import_suggestions(self) -> None:
        if not self.import_review_suggestions:
            return
        for row, suggestion in enumerate(self.import_review_suggestions):
            item = self.import_review_table.item(row, 0)
            if item is None:
                continue
            item.setCheckState(
                Qt.CheckState.Checked
                if suggestion.confidence >= self.existing_machine_import_service.high_confidence_threshold
                else Qt.CheckState.Unchecked
            )

    def _selected_import_suggestions(self) -> list[ImportSuggestion]:
        selected: list[ImportSuggestion] = []
        for row, suggestion in enumerate(self.import_review_suggestions):
            item = self.import_review_table.item(row, 0)
            if item is None:
                continue
            if item.checkState() != Qt.CheckState.Checked:
                continue
            selected.append(
                ImportSuggestion(
                    field=suggestion.field,
                    value=suggestion.value,
                    confidence=suggestion.confidence,
                    reason=suggestion.reason,
                    source_file=suggestion.source_file,
                    auto_apply=True,
                )
            )
        return selected

    def _apply_selected_import_suggestions(self) -> None:
        if self.current_import_profile is None:
            self._show_error("Import Review", "No imported machine profile is loaded.")
            return

        selected = self._selected_import_suggestions()
        if not selected:
            self._show_error("Import Review", "Select at least one suggestion to apply.")
            return

        try:
            base_project = self._build_project_from_ui()
        except Exception:  # noqa: BLE001
            if self.current_project is not None:
                base_project = self.current_project
            else:
                suggested_preset: str | None = None
                for suggestion in selected:
                    if suggestion.field != "preset_id":
                        continue
                    if isinstance(suggestion.value, str) and suggestion.value.strip():
                        suggested_preset = suggestion.value.strip()
                        break

                target_index = -1
                if suggested_preset:
                    target_index = self.preset_combo.findData(suggested_preset)
                if target_index < 0:
                    target_index = self.preset_combo.findData(self.DEFAULT_VORON_PRESET_ID)
                if target_index < 0 and self.preset_combo.count() > 1:
                    target_index = 1
                if target_index >= 0:
                    self.preset_combo.setCurrentIndex(target_index)

                try:
                    base_project = self._build_project_from_ui()
                except Exception:  # noqa: BLE001
                    self._show_error(
                        "Import Review",
                        "Current UI project is invalid and cannot be updated.",
                    )
                    return

        profile = self.current_import_profile.model_copy(deep=True)
        profile.suggestions = selected

        try:
            updated_project = self.existing_machine_import_service.apply_suggestions(profile, base_project)
        except Exception as exc:  # noqa: BLE001
            self._show_error("Import Review", str(exc))
            return

        self._apply_project_to_ui(updated_project)
        self._render_and_validate()
        self.import_profile_applied_snapshot = updated_project.model_dump(mode="json")
        self.statusBar().showMessage(f"Applied {len(selected)} import suggestion(s)", 3000)

    def _refresh_saved_machine_profiles(self, select_name: str | None = None) -> None:
        names = self.saved_machine_profile_service.list_names()
        self.machine_profile_combo.blockSignals(True)
        self.machine_profile_combo.clear()
        self.machine_profile_combo.addItems(names)
        self.machine_profile_combo.blockSignals(False)
        target = (select_name or "").strip()
        if target:
            index = self.machine_profile_combo.findText(target)
            if index >= 0:
                self.machine_profile_combo.setCurrentIndex(index)
        elif self.machine_profile_combo.count() > 0:
            self.machine_profile_combo.setCurrentIndex(0)

    def _save_current_machine_profile(self) -> None:
        if self.current_import_profile is None:
            self._show_error("Machine Profile", "Import a machine first before saving a profile.")
            return
        name = self.machine_profile_name_edit.text().strip() or self.current_import_profile.name
        if not name:
            self._show_error("Machine Profile", "Profile name is required.")
            return

        profile = self.current_import_profile.model_copy(deep=True)
        profile.name = name
        profile.suggestions = self._selected_import_suggestions() or profile.suggestions
        profile.detected["file_map"] = dict(self.imported_file_map)
        if self.import_profile_applied_snapshot:
            profile.detected["applied_project_snapshot"] = dict(self.import_profile_applied_snapshot)

        try:
            self.saved_machine_profile_service.save(name, profile)
        except Exception as exc:  # noqa: BLE001
            self._show_error("Machine Profile", str(exc))
            return

        self._refresh_saved_machine_profiles(select_name=name)
        self.statusBar().showMessage(f"Saved machine profile '{name}'", 3000)

    def _load_selected_machine_profile(self) -> None:
        name = self.machine_profile_combo.currentText().strip()
        if not name:
            self._show_error("Machine Profile", "Select a saved machine profile.")
            return
        profile = self.saved_machine_profile_service.load(name)
        if profile is None:
            self._show_error("Machine Profile", f"Profile '{name}' was not found.")
            self._refresh_saved_machine_profiles()
            return
        file_map = profile.detected.get("file_map")
        if not isinstance(file_map, dict) or not file_map:
            self._show_error(
                "Machine Profile",
                f"Profile '{name}' does not contain an imported file map.",
            )
            return

        normalized_file_map = {str(path): str(content) for path, content in file_map.items()}
        self._load_imported_machine_profile(profile, normalized_file_map)

        snapshot = profile.detected.get("applied_project_snapshot")
        if isinstance(snapshot, dict):
            self.import_profile_applied_snapshot = dict(snapshot)
        else:
            self.import_profile_applied_snapshot = {}
        self.statusBar().showMessage(f"Loaded machine profile '{name}'", 3000)

    def _delete_selected_machine_profile(self) -> None:
        name = self.machine_profile_combo.currentText().strip()
        if not name:
            self._show_error("Machine Profile", "Select a saved machine profile to delete.")
            return
        answer = QMessageBox.question(
            self,
            "Delete Machine Profile",
            f"Delete saved machine profile '{name}'?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        deleted = self.saved_machine_profile_service.delete(name)
        if not deleted:
            self._show_error("Machine Profile", f"Profile '{name}' was not found.")
            return
        self._refresh_saved_machine_profiles()
        self.statusBar().showMessage(f"Deleted machine profile '{name}'", 3000)

    def _show_selected_imported_file(self) -> None:
        item = self.generated_file_list.currentItem()
        if item is None:
            return
        file_path = item.text().strip()
        if not file_path:
            return
        content = self.imported_file_map.get(file_path)
        if content is None:
            return
        self._set_files_tab_content(
            content=content,
            label=file_path,
            source="imported",
            generated_name=None,
        )

    def _build_main_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setSpacing(14)

        title = QLabel("KlippConfig Main", tab)
        title.setStyleSheet("QLabel { font-size: 24px; font-weight: 700; }")
        layout.addWidget(title)

        subtitle = QLabel(
            "Choose a workflow to get started.",
            tab,
        )
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        actions_group = QGroupBox("Start", tab)
        actions_layout = QGridLayout(actions_group)
        actions_layout.setHorizontalSpacing(16)
        actions_layout.setVerticalSpacing(16)

        button_style = (
            "QPushButton {"
            " min-height: 110px;"
            " font-size: 17px;"
            " font-weight: 700;"
            " text-align: left;"
            " padding: 14px;"
            " border-radius: 10px;"
            "}"
        )

        def _build_grid_cell(
            parent: QWidget,
            button: QPushButton,
            description_text: str,
        ) -> QWidget:
            cell = QWidget(parent)
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(8)
            button.setStyleSheet(button_style)
            cell_layout.addWidget(button)
            description = QLabel(description_text, cell)
            description.setWordWrap(True)
            description.setStyleSheet("QLabel { color: #9ca3af; }")
            cell_layout.addWidget(description)
            return cell

        self.main_new_firmware_btn = QPushButton("New Firmware", actions_group)
        self.main_new_firmware_btn.clicked.connect(self._go_to_configuration_tab)
        actions_layout.addWidget(
            _build_grid_cell(
                actions_group,
                self.main_new_firmware_btn,
                "Open Configuration to build a new printer profile from presets.",
            ),
            0,
            0,
        )

        self.main_modify_existing_btn = QPushButton("Modify Existing", actions_group)
        self.main_modify_existing_btn.clicked.connect(self._go_to_modify_existing_tab)
        actions_layout.addWidget(
            _build_grid_cell(
                actions_group,
                self.main_modify_existing_btn,
                "Open the remote workflow for live SSH config editing, upload, and restart testing.",
            ),
            0,
            1,
        )

        self.main_connect_manage_btn = QPushButton("Connect/Manage Printer", actions_group)
        self.main_connect_manage_btn.clicked.connect(self._go_to_ssh_tab)
        actions_layout.addWidget(
            _build_grid_cell(
                actions_group,
                self.main_connect_manage_btn,
                "Open Printer Connection, then use Manage Printer for direct file operations.",
            ),
            1,
            0,
        )

        self.main_about_btn = QPushButton("About", actions_group)
        self.main_about_btn.clicked.connect(self._show_about_window)
        actions_layout.addWidget(
            _build_grid_cell(
                actions_group,
                self.main_about_btn,
                "Open About for mission, creator info, and platform details.",
            ),
            1,
            1,
        )

        actions_layout.setColumnStretch(0, 1)
        actions_layout.setColumnStretch(1, 1)

        layout.addWidget(actions_group)
        layout.addStretch(1)
        return tab

    def _refresh_modify_connection_summary(self) -> None:
        if not hasattr(self, "modify_connection_summary_label"):
            return

        host = self.ssh_host_edit.text().strip() if hasattr(self, "ssh_host_edit") else ""
        username = self.ssh_username_edit.text().strip() if hasattr(self, "ssh_username_edit") else ""
        port = self.ssh_port_spin.value() if hasattr(self, "ssh_port_spin") else 22
        key_path = self.ssh_key_path_edit.text().strip() if hasattr(self, "ssh_key_path_edit") else ""
        auth_mode = "SSH key" if key_path else "password/agent"

        if host and username:
            summary = f"{username}@{host}:{port} ({auth_mode})"
        elif host:
            summary = f"{host}:{port} (set username on SSH tab)"
        else:
            summary = "Set host and username on SSH tab."
        self.modify_connection_summary_label.setText(summary)

        if hasattr(self, "modify_restart_command_label"):
            restart_command = ""
            if hasattr(self, "ssh_restart_cmd_edit"):
                restart_command = self.ssh_restart_cmd_edit.text().strip()
            self.modify_restart_command_label.setText(
                restart_command or "sudo systemctl restart klipper"
            )

    def _sync_modify_remote_cfg_from_ssh(self, value: str) -> None:
        if not hasattr(self, "modify_remote_cfg_path_edit"):
            return
        if self.modify_current_remote_file:
            return
        if not self.modify_remote_cfg_path_edit.text().strip():
            self.modify_remote_cfg_path_edit.setText(value.strip())

    def _build_modify_existing_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        intro = QLabel(
            "Remote-only workflow: connect over SSH, open a .cfg file, modify/refactor/validate, "
            "upload with backup, then run restart/status test.",
            tab,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        target_group = QGroupBox("SSH Target", tab)
        target_form = QFormLayout(target_group)

        self.modify_connected_printer_label = QLabel("No active SSH connection.", target_group)
        self.modify_connected_printer_label.setWordWrap(True)
        target_form.addRow("Connected", self.modify_connected_printer_label)

        self.modify_connection_summary_label = QLabel("", target_group)
        self.modify_connection_summary_label.setWordWrap(True)
        target_form.addRow("Using SSH", self.modify_connection_summary_label)

        self.modify_remote_cfg_path_edit = QLineEdit(target_group)
        self.modify_remote_cfg_path_edit.setText(
            self.ssh_remote_fetch_path_edit.text().strip() or "~/printer_data/config/printer.cfg"
        )
        target_form.addRow("Remote .cfg path", self.modify_remote_cfg_path_edit)

        self.modify_backup_root_edit = QLineEdit(target_group)
        self.modify_backup_root_edit.setText("~/klippconfig_backups")
        target_form.addRow("Backup root", self.modify_backup_root_edit)

        self.modify_restart_command_label = QLabel("", target_group)
        self.modify_restart_command_label.setWordWrap(True)
        target_form.addRow("Restart command", self.modify_restart_command_label)

        layout.addWidget(target_group)

        action_row = QHBoxLayout()

        self.modify_connect_btn = QPushButton("Connect", tab)
        self.modify_connect_btn.clicked.connect(self._modify_connect)
        action_row.addWidget(self.modify_connect_btn)

        self.modify_open_remote_btn = QPushButton("Open Remote .cfg", tab)
        self.modify_open_remote_btn.clicked.connect(self._modify_open_remote_cfg)
        action_row.addWidget(self.modify_open_remote_btn)

        self.modify_refactor_btn = QPushButton("Refactor", tab)
        self.modify_refactor_btn.clicked.connect(self._modify_refactor_current_file)
        action_row.addWidget(self.modify_refactor_btn)

        action_row.addStretch(1)
        layout.addLayout(action_row)
        primary_actions_hint = QLabel(
            "Primary actions run from command bar: Configuration -> Validate Current, "
            "Printer -> Upload Current, Printer -> Restart Klipper.",
            tab,
        )
        primary_actions_hint.setWordWrap(True)
        layout.addWidget(primary_actions_hint)

        self.modify_status_label = QLabel("No remote file loaded.", tab)
        self.modify_status_label.setWordWrap(True)
        layout.addWidget(self.modify_status_label)

        self.modify_editor = QPlainTextEdit(tab)
        self.modify_editor.textChanged.connect(self._update_action_enablement)
        layout.addWidget(self.modify_editor, 1)

        self.ssh_host_edit.textChanged.connect(self._refresh_modify_connection_summary)
        self.ssh_username_edit.textChanged.connect(self._refresh_modify_connection_summary)
        self.ssh_port_spin.valueChanged.connect(self._refresh_modify_connection_summary)
        self.ssh_key_path_edit.textChanged.connect(self._refresh_modify_connection_summary)
        self.ssh_restart_cmd_edit.textChanged.connect(self._refresh_modify_connection_summary)
        self.ssh_remote_fetch_path_edit.textChanged.connect(self._sync_modify_remote_cfg_from_ssh)

        self._refresh_modify_connection_summary()
        return tab

    def _build_about_view(self, parent: QWidget) -> QWidget:
        view = QWidget(parent)
        layout = QVBoxLayout(view)

        scroll = QScrollArea(view)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, 1)

        content = QWidget(scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(12)

        title = QLabel("About KlippConfig", content)
        title.setStyleSheet("QLabel { font-size: 22px; font-weight: 700; }")
        content_layout.addWidget(title)

        subtitle = QLabel(
            "KlippConfig is a desktop toolkit for configuring, validating, and managing Klipper firmware setups.",
            content,
        )
        subtitle.setWordWrap(True)
        content_layout.addWidget(subtitle)

        version_label = QLabel(f"Version: {__version__}", content)
        version_label.setStyleSheet("QLabel { color: #9ca3af; font-size: 12px; }")
        content_layout.addWidget(version_label)

        quote_group = QGroupBox("Mission Quote", content)
        quote_layout = QVBoxLayout(quote_group)
        self.about_quote_label = QLabel(
            "\"We need easier accessibility to control 3D printers and their firmware.\"",
            quote_group,
        )
        self.about_quote_label.setWordWrap(True)
        self.about_quote_label.setStyleSheet(
            "QLabel { font-style: italic; font-size: 15px; color: #f3f4f6; "
            "background-color: #1f2937; border: 1px solid #4b5563; border-radius: 6px; padding: 10px; }"
        )
        quote_layout.addWidget(self.about_quote_label)
        content_layout.addWidget(quote_group)

        creator_group = QGroupBox("Creator", content)
        creator_layout = QHBoxLayout(creator_group)

        self.about_creator_icon_label = QLabel(creator_group)
        self.about_creator_icon_label.setFixedSize(120, 120)
        self.about_creator_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        creator_icon = creator_icon_path()
        if creator_icon.exists():
            pixmap = QPixmap(str(creator_icon))
            if not pixmap.isNull():
                self.about_creator_icon_label.setPixmap(
                    pixmap.scaled(
                        110,
                        110,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:
                self.about_creator_icon_label.setText("Creator icon\nunavailable")
        else:
            self.about_creator_icon_label.setText("Creator icon\nmissing")
        creator_layout.addWidget(self.about_creator_icon_label)

        creator_text = QLabel(
            "Built to reduce friction across printer setup and ongoing firmware maintenance.\n\n"
            "The goal is to give makers one place to configure hardware profiles, validate risky changes, "
            "and manage live printer files without hopping between disconnected tools.",
            creator_group,
        )
        creator_text.setWordWrap(True)
        creator_layout.addWidget(creator_text, 1)
        content_layout.addWidget(creator_group)

        details_group = QGroupBox("Platform Overview", content)
        details_layout = QVBoxLayout(details_group)
        details_label = QLabel(
            "Core capabilities:\n"
            "- Preset-driven Voron config generation.\n"
            "- Live validation for conflicts and config issues.\n"
            "- SSH connect/deploy workflows with saved connection profiles.\n"
            "- Manage Printer tools for remote file editing, backups, restore, and control window access.\n"
            "- Local and remote .cfg refactor + validation flows.",
            details_group,
        )
        details_label.setWordWrap(True)
        details_layout.addWidget(details_label)
        content_layout.addWidget(details_group)

        community_group = QGroupBox("Community", content)
        community_layout = QVBoxLayout(community_group)
        discord_label = QLabel(
            'Join the KlippConfig Discord: <a href="https://discord.gg/4CthQzS7Qy">https://discord.gg/4CthQzS7Qy</a>',
            community_group,
        )
        discord_label.setOpenExternalLinks(True)
        discord_label.setWordWrap(True)
        community_layout.addWidget(discord_label)
        content_layout.addWidget(community_group)

        content_layout.addStretch(1)
        scroll.setWidget(content)
        return view

    def _build_wizard_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        top_row = QHBoxLayout()
        self.render_validate_btn = QPushButton("Compile", tab)
        self.render_validate_btn.clicked.connect(self._render_and_validate)
        top_row.addWidget(self.render_validate_btn)

        self.preset_notes_label = QLabel("", tab)
        self.preset_notes_label.setWordWrap(True)
        top_row.addWidget(self.preset_notes_label, 1)
        layout.addLayout(top_row, 0)

        grid = QGridLayout()
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 3)

        wizard_group = QGroupBox("Core Hardware", tab)
        wizard_form = QFormLayout(wizard_group)

        self.vendor_combo = QComboBox(wizard_group)
        self.vendor_combo.addItem("None", "")
        self.vendor_combo.addItem("custom printer", "custom_printer")
        self.vendor_combo.addItem("Voron", "voron")
        self.vendor_combo.setCurrentIndex(0)
        self.vendor_combo.currentIndexChanged.connect(self._on_vendor_changed)
        wizard_form.addRow("Vendor", self.vendor_combo)

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

        self.probe_type_combo = QComboBox(wizard_group)
        self.probe_type_combo.setEditable(True)
        self.probe_type_combo.currentTextChanged.connect(self._render_and_validate)
        wizard_form.addRow("Probe", self.probe_type_combo)

        self.toolhead_usb_board_combo = QComboBox(wizard_group)
        self.toolhead_usb_board_combo.currentIndexChanged.connect(self._on_toolhead_usb_board_changed)
        wizard_form.addRow("USB toolhead board", self.toolhead_usb_board_combo)

        self.toolhead_can_board_combo = QComboBox(wizard_group)
        self.toolhead_can_board_combo.currentIndexChanged.connect(self._on_toolhead_can_board_changed)
        wizard_form.addRow("CAN toolhead board", self.toolhead_can_board_combo)

        # Backward-compatible alias used in older tests/helpers.
        self.toolhead_board_combo = self.toolhead_can_board_combo

        self.toolhead_canbus_uuid_edit = QLineEdit(wizard_group)
        self.toolhead_canbus_uuid_edit.setPlaceholderText("replace-with-canbus-uuid")
        self.toolhead_canbus_uuid_edit.textChanged.connect(self._render_and_validate)
        wizard_form.addRow("CAN UUID", self.toolhead_canbus_uuid_edit)

        self.hotend_thermistor_edit = QLineEdit(wizard_group)
        self.hotend_thermistor_edit.setPlaceholderText("Hotend Thermistor")
        self.hotend_thermistor_edit.textChanged.connect(self._render_and_validate)
        wizard_form.addRow("Hotend thermistor", self.hotend_thermistor_edit)

        self.bed_thermistor_edit = QLineEdit(wizard_group)
        self.bed_thermistor_edit.setPlaceholderText("Bed Thermistor")
        self.bed_thermistor_edit.textChanged.connect(self._render_and_validate)
        wizard_form.addRow("Bed thermistor", self.bed_thermistor_edit)

        grid.addWidget(wizard_group, 0, 0)

        options_group = QGroupBox("Macro Packs", tab)
        options_layout = QVBoxLayout(options_group)
        options_layout.setSpacing(6)

        (
            macros_section,
            self.macros_section_toggle,
            self.macros_section_content,
            macros_section_layout,
        ) = self._build_collapsible_section("Macro Packs", options_group, expanded=True)
        self.macros_group = QWidget(self.macros_section_content)
        macros_layout = QVBoxLayout(self.macros_group)
        macros_layout.setContentsMargins(0, 0, 0, 0)
        macros_layout.setSpacing(4)
        self.macro_checkboxes: dict[str, QCheckBox] = {}
        for key, label in self.MACRO_PACK_OPTIONS.items():
            checkbox = QCheckBox(label, self.macros_group)
            checkbox.setObjectName(f"macro_{key}")
            checkbox.toggled.connect(
                lambda checked, name=key: self._on_macro_checkbox_toggled(name, checked)
            )
            macros_layout.addWidget(checkbox)
            self.macro_checkboxes[key] = checkbox
        macros_layout.addStretch(1)
        macros_section_layout.addWidget(self.macros_group)
        options_layout.addWidget(macros_section)

        (
            addons_section,
            self.addons_section_toggle,
            self.addons_section_content,
            addons_section_layout,
        ) = self._build_collapsible_section("Add-ons", options_group, expanded=True)
        self.addons_group = QWidget(self.addons_section_content)
        addons_layout = QVBoxLayout(self.addons_group)
        addons_layout.setContentsMargins(0, 0, 0, 0)
        addons_layout.setSpacing(4)
        self.addons_layout = addons_layout
        self.addon_checkboxes: dict[str, QCheckBox] = {}
        for key, label in self.addon_options.items():
            checkbox = QCheckBox(label, self.addons_group)
            checkbox.setObjectName(f"addon_{key}")
            checkbox.toggled.connect(
                lambda checked, name=key: self._on_addon_checkbox_toggled(name, checked)
            )
            addons_layout.addWidget(checkbox)
            self.addon_checkboxes[key] = checkbox
        addons_layout.addStretch(1)
        addons_section_layout.addWidget(self.addons_group)
        options_layout.addWidget(addons_section)
        addons_section.setVisible(False)

        (
            led_section,
            self.led_section_toggle,
            self.led_section_content,
            led_section_layout,
        ) = self._build_collapsible_section("LED Control", options_group, expanded=False)
        self.led_group = QWidget(self.led_section_content)
        led_form = QFormLayout(self.led_group)
        led_form.setContentsMargins(0, 0, 0, 0)

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

        led_section_layout.addWidget(self.led_group)
        options_layout.addWidget(led_section)

        (
            addon_details_section,
            self.addon_details_section_toggle,
            self.addon_details_section_content,
            addon_details_layout,
        ) = self._build_collapsible_section(
            "Add-on Package Details",
            options_group,
            expanded=False,
        )
        self.addon_package_details_view = QPlainTextEdit(self.addon_details_section_content)
        self.addon_package_details_view.setReadOnly(True)
        self.addon_package_details_view.setPlaceholderText(
            "Add-on support is temporarily disabled in this build."
        )
        addon_details_layout.addWidget(self.addon_package_details_view, 1)
        options_layout.addWidget(addon_details_section)
        addon_details_section.setVisible(False)

        grid.addWidget(options_group, 1, 0)

        machine_attr_group = QGroupBox("Machine Attributes", tab)
        machine_attr_layout = QVBoxLayout(machine_attr_group)
        machine_attr_layout.setSpacing(6)

        (
            mcu_section,
            self.machine_attr_mcu_toggle,
            self.machine_attr_mcu_content,
            mcu_section_layout,
        ) = self._build_collapsible_section(
            "MCU Map",
            machine_attr_group,
            expanded=False,
        )
        self.machine_attr_mcu_view = QPlainTextEdit(self.machine_attr_mcu_content)
        self.machine_attr_mcu_view.setReadOnly(True)
        mcu_section_layout.addWidget(self.machine_attr_mcu_view, 1)
        machine_attr_layout.addWidget(mcu_section)

        (
            motion_section,
            self.machine_attr_motion_toggle,
            self.machine_attr_motion_content,
            motion_section_layout,
        ) = self._build_collapsible_section(
            "Motion and Drivers",
            machine_attr_group,
            expanded=False,
        )
        self.machine_attr_motion_view = QPlainTextEdit(self.machine_attr_motion_content)
        self.machine_attr_motion_view.setReadOnly(True)
        motion_section_layout.addWidget(self.machine_attr_motion_view, 1)
        machine_attr_layout.addWidget(motion_section)

        (
            probe_section,
            self.machine_attr_probe_toggle,
            self.machine_attr_probe_content,
            probe_section_layout,
        ) = self._build_collapsible_section(
            "Probe + Leveling + Mesh",
            machine_attr_group,
            expanded=False,
        )
        self.machine_attr_probe_view = QPlainTextEdit(self.machine_attr_probe_content)
        self.machine_attr_probe_view.setReadOnly(True)
        probe_section_layout.addWidget(self.machine_attr_probe_view, 1)
        machine_attr_layout.addWidget(probe_section)

        (
            thermal_section,
            self.machine_attr_thermal_toggle,
            self.machine_attr_thermal_content,
            thermal_section_layout,
        ) = self._build_collapsible_section(
            "Thermal + Fan + Sensors",
            machine_attr_group,
            expanded=False,
        )
        self.machine_attr_thermal_view = QPlainTextEdit(self.machine_attr_thermal_content)
        self.machine_attr_thermal_view.setReadOnly(True)
        thermal_section_layout.addWidget(self.machine_attr_thermal_view, 1)
        machine_attr_layout.addWidget(thermal_section)

        grid.addWidget(machine_attr_group, 2, 0)

        package_splitter = QSplitter(Qt.Orientation.Horizontal, tab)
        self.wizard_package_splitter = package_splitter

        package_list_group = QGroupBox("Project Package", package_splitter)
        self.wizard_package_list_group = package_list_group
        package_list_layout = QVBoxLayout(package_list_group)
        self.wizard_package_file_list = QListWidget(package_list_group)
        self.wizard_package_file_list.itemSelectionChanged.connect(
            self._on_wizard_package_file_selected
        )
        package_list_layout.addWidget(self.wizard_package_file_list, 1)

        package_preview_group = QGroupBox("Selected File Preview", package_splitter)
        package_preview_layout = QVBoxLayout(package_preview_group)
        self.wizard_package_preview_label = QLabel("No generated files.", package_preview_group)
        self.wizard_package_preview_label.setWordWrap(True)
        package_preview_layout.addWidget(self.wizard_package_preview_label)
        self.wizard_package_preview = QPlainTextEdit(package_preview_group)
        self.wizard_package_preview.setReadOnly(True)
        self.wizard_package_preview.setPlaceholderText(
            "Compile to generate package files and preview their contents."
        )
        package_preview_layout.addWidget(self.wizard_package_preview, 1)

        package_splitter.setStretchFactor(0, 1)
        package_splitter.setStretchFactor(1, 3)
        grid.addWidget(package_splitter, 0, 1, 3, 1)

        layout.addLayout(grid, 1)
        return tab

    def _build_files_tab_experimental(self) -> QWidget:
        tab = self._build_files_tab()
        tab.setObjectName("files_tab_material_v1")
        layout = tab.layout()
        if isinstance(layout, QVBoxLayout):
            layout.setContentsMargins(10, 10, 10, 10)
            layout.setSpacing(8)

        if hasattr(self, "files_top_command_bar"):
            self.files_top_command_bar.setObjectName("files_top_command_bar")
        if hasattr(self, "files_show_generated_btn"):
            self.files_show_generated_btn.setObjectName("files_tonal_action")
        if hasattr(self, "apply_form_btn"):
            self.apply_form_btn.setObjectName("files_primary_action")
        if hasattr(self, "refactor_cfg_btn"):
            self.refactor_cfg_btn.setObjectName("files_tonal_action")
        if hasattr(self, "preview_path_label"):
            self.preview_path_label.setObjectName("files_path_label")
        if hasattr(self, "files_breadcrumbs_label"):
            self.files_breadcrumbs_label.setObjectName("files_path_label")
        if hasattr(self, "generated_file_list"):
            self.generated_file_list.setObjectName("files_generated_list")
        if hasattr(self, "file_view_tabs"):
            self.file_view_tabs.setObjectName("files_view_tabs")
        if hasattr(self, "validation_table"):
            self.validation_table.setObjectName("files_validation_table")
        if hasattr(self, "import_review_table"):
            self.import_review_table.setObjectName("files_import_review_table")

        self._install_files_experiment_chip_row(tab)
        self._update_files_experiment_chips(blocking=0, warnings=0, source_label="")
        return tab

    def _install_files_experiment_chip_row(self, tab: QWidget) -> None:
        layout = tab.layout()
        if not isinstance(layout, QVBoxLayout):
            return
        if hasattr(self, "files_primary_status_chip"):
            return

        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(8)

        self.files_primary_status_chip = QLabel("No file loaded", tab)
        self.files_blocking_chip = QLabel("Blocking: 0", tab)
        self.files_warning_chip = QLabel("Warnings: 0", tab)
        self.files_dirty_chip = QLabel("Saved", tab)
        for chip in (
            self.files_primary_status_chip,
            self.files_blocking_chip,
            self.files_warning_chip,
            self.files_dirty_chip,
        ):
            chip.setObjectName("files_chip")
            chip.setProperty("chipSeverity", "info")
            chip_row.addWidget(chip)
        chip_row.addStretch(1)

        layout.insertLayout(4, chip_row)

    @staticmethod
    def _set_files_chip_state(chip: QLabel, *, text: str, severity: str) -> None:
        chip.setText(text)
        chip.setProperty("chipSeverity", severity)
        chip.style().unpolish(chip)
        chip.style().polish(chip)
        chip.update()

    def _update_files_experiment_chips(self, *, blocking: int, warnings: int, source_label: str) -> None:
        if not self._is_files_experiment_enabled():
            return
        if not hasattr(self, "files_primary_status_chip"):
            return

        is_cfg = self._is_cfg_label(self.files_current_label, self.files_current_generated_name)
        dirty = bool(self.app_state_store.snapshot().active_file.dirty)
        source = source_label or self._current_cfg_target_label()
        if not self.files_current_content.strip():
            summary = "No file loaded"
            severity = "info"
        elif not is_cfg:
            summary = "Preview only (non-.cfg file)"
            severity = "info"
        elif blocking > 0:
            summary = f"Action needed: {source}"
            severity = "error"
        elif warnings > 0:
            summary = f"Heads up: {source}"
            severity = "warning"
        elif dirty:
            summary = f"Unsaved changes: {source}"
            severity = "dirty"
        else:
            summary = f"Ready: {source}"
            severity = "success"

        self._set_files_chip_state(
            self.files_primary_status_chip,
            text=summary,
            severity=severity,
        )
        self._set_files_chip_state(
            self.files_blocking_chip,
            text=f"Blocking: {max(0, int(blocking))}",
            severity=("error" if blocking > 0 else "info"),
        )
        self._set_files_chip_state(
            self.files_warning_chip,
            text=f"Warnings: {max(0, int(warnings))}",
            severity=("warning" if warnings > 0 else "info"),
        )
        self._set_files_chip_state(
            self.files_dirty_chip,
            text=("Unsaved" if dirty else "Saved"),
            severity=("dirty" if dirty else "success"),
        )

    def _build_files_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.files_top_command_bar = QWidget(tab)
        top_row = QHBoxLayout(self.files_top_command_bar)
        top_row.setContentsMargins(8, 8, 8, 8)
        top_row.setSpacing(8)

        self.files_show_generated_btn = QPushButton("Show Generated Files", self.files_top_command_bar)
        self.files_show_generated_btn.clicked.connect(self._show_selected_generated_file)
        top_row.addWidget(self.files_show_generated_btn)

        self.apply_form_btn = QPushButton("Apply Form Changes", self.files_top_command_bar)
        self.apply_form_btn.clicked.connect(self._apply_cfg_form_changes)
        top_row.addWidget(self.apply_form_btn)

        self.refactor_cfg_btn = QPushButton("Refactor Current .cfg", self.files_top_command_bar)
        self.refactor_cfg_btn.clicked.connect(self._refactor_current_cfg_file)
        top_row.addWidget(self.refactor_cfg_btn)

        self.preview_path_label = QLabel("No file selected.", self.files_top_command_bar)
        top_row.addWidget(self.preview_path_label, 1)

        layout.addWidget(self.files_top_command_bar)
        self.files_breadcrumbs_label = QLabel("Path: none", tab)
        self.files_breadcrumbs_label.setWordWrap(True)
        layout.addWidget(self.files_breadcrumbs_label)

        profile_row = QHBoxLayout()
        self.machine_profile_name_edit = QLineEdit(tab)
        self.machine_profile_name_edit.setPlaceholderText("Machine profile name")
        profile_row.addWidget(self.machine_profile_name_edit, 1)

        self.machine_profile_combo = QComboBox(tab)
        self.machine_profile_combo.setMinimumWidth(220)
        profile_row.addWidget(self.machine_profile_combo)

        self.save_machine_profile_btn = QPushButton("Save Machine Profile", tab)
        self.save_machine_profile_btn.clicked.connect(self._save_current_machine_profile)
        profile_row.addWidget(self.save_machine_profile_btn)

        self.load_machine_profile_btn = QPushButton("Load Machine Profile", tab)
        self.load_machine_profile_btn.clicked.connect(self._load_selected_machine_profile)
        profile_row.addWidget(self.load_machine_profile_btn)

        self.delete_machine_profile_btn = QPushButton("Delete", tab)
        self.delete_machine_profile_btn.clicked.connect(self._delete_selected_machine_profile)
        profile_row.addWidget(self.delete_machine_profile_btn)

        layout.addLayout(profile_row)

        self.cfg_tools_status_label = QLabel("", tab)
        self.cfg_tools_status_label.setWordWrap(True)
        self.cfg_tools_status_label.setVisible(False)
        layout.addWidget(self.cfg_tools_status_label)

        splitter = QSplitter(Qt.Horizontal, tab)
        self.files_splitter = splitter
        self.generated_file_list = QListWidget(splitter)
        self.generated_file_list.itemSelectionChanged.connect(self._on_generated_file_selected)
        self.generated_file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.generated_file_list.customContextMenuRequested.connect(
            self._show_generated_files_context_menu
        )

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
            import_review_section,
            self.import_review_section_toggle,
            self.import_review_section_content,
            import_review_layout,
        ) = self._build_collapsible_section("Import Review", tab, expanded=True)

        self.import_review_status_label = QLabel(
            "No imported machine analysis loaded.",
            self.import_review_section_content,
        )
        self.import_review_status_label.setWordWrap(True)
        import_review_layout.addWidget(self.import_review_status_label)

        self.import_review_table = QTableWidget(self.import_review_section_content)
        self.import_review_table.setColumnCount(6)
        self.import_review_table.setHorizontalHeaderLabels(
            ["Apply", "Field", "Value", "Confidence", "Reason", "Source"]
        )
        self.import_review_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.import_review_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        self.import_review_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeToContents
        )
        self.import_review_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents
        )
        self.import_review_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.import_review_table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeToContents
        )
        self.import_review_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.import_review_table.setSelectionMode(QAbstractItemView.SingleSelection)
        import_review_layout.addWidget(self.import_review_table)

        import_actions = QHBoxLayout()
        self.import_apply_selected_btn = QPushButton("Apply Selected", self.import_review_section_content)
        self.import_apply_selected_btn.clicked.connect(self._apply_selected_import_suggestions)
        import_actions.addWidget(self.import_apply_selected_btn)

        self.import_select_high_btn = QPushButton("Select High Confidence", self.import_review_section_content)
        self.import_select_high_btn.clicked.connect(self._select_high_confidence_import_suggestions)
        import_actions.addWidget(self.import_select_high_btn)
        import_actions.addStretch(1)
        import_review_layout.addLayout(import_actions)
        layout.addWidget(import_review_section)

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

        validate_btn = QPushButton("Compile", self.overrides_section_content)
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

        saved_row = QWidget(connection_group)
        saved_layout = QHBoxLayout(saved_row)
        saved_layout.setContentsMargins(0, 0, 0, 0)
        self.ssh_saved_connection_combo = QComboBox(saved_row)
        self.ssh_saved_connection_combo.setMinimumWidth(220)
        saved_layout.addWidget(self.ssh_saved_connection_combo, 1)

        load_saved_btn = QPushButton("Load", saved_row)
        load_saved_btn.clicked.connect(self._load_selected_saved_connection)
        saved_layout.addWidget(load_saved_btn)

        save_saved_btn = QPushButton("Save", saved_row)
        save_saved_btn.clicked.connect(self._save_current_connection_profile)
        saved_layout.addWidget(save_saved_btn)

        delete_saved_btn = QPushButton("Delete", saved_row)
        delete_saved_btn.clicked.connect(self._delete_selected_saved_connection)
        saved_layout.addWidget(delete_saved_btn)
        connection_form.addRow("Saved", saved_row)

        self.ssh_connection_name_edit = QLineEdit(connection_group)
        self.ssh_connection_name_edit.setPlaceholderText("My Printer")
        connection_form.addRow("Connection name", self.ssh_connection_name_edit)

        default_row = QWidget(connection_group)
        default_layout = QHBoxLayout(default_row)
        default_layout.setContentsMargins(0, 0, 0, 0)

        self.ssh_default_connection_label = QLabel("Default: (none)", default_row)
        self.ssh_default_connection_label.setWordWrap(True)
        default_layout.addWidget(self.ssh_default_connection_label, 1)

        self.ssh_set_default_btn = QPushButton("Set Default", default_row)
        self.ssh_set_default_btn.clicked.connect(self._set_default_saved_connection_from_selection)
        default_layout.addWidget(self.ssh_set_default_btn)

        self.ssh_clear_default_btn = QPushButton("Clear Default", default_row)
        self.ssh_clear_default_btn.clicked.connect(self._clear_default_saved_connection)
        default_layout.addWidget(self.ssh_clear_default_btn)
        connection_form.addRow(default_row)

        self.ssh_save_on_success_checkbox = QCheckBox(
            "Save on successful connect (uses Connection name)",
            connection_group,
        )
        self.ssh_save_on_success_checkbox.setChecked(True)
        connection_form.addRow(self.ssh_save_on_success_checkbox)

        self.ssh_auto_connect_checkbox = QCheckBox(
            "Auto-connect on launch",
            connection_group,
        )
        self.ssh_auto_connect_checkbox.setChecked(self.auto_connect_enabled)
        self.ssh_auto_connect_checkbox.toggled.connect(self._set_auto_connect_enabled)
        connection_form.addRow(self.ssh_auto_connect_checkbox)

        self.ssh_remote_dir_edit = QLineEdit(connection_group)
        self.ssh_remote_dir_edit.setText("~/printer_data/config")
        connection_form.addRow("Remote cfg dir", self.ssh_remote_dir_edit)

        self.ssh_remote_fetch_path_edit = QLineEdit(connection_group)
        self.ssh_remote_fetch_path_edit.setText("~/printer_data/config/printer.cfg")
        connection_form.addRow("Remote file", self.ssh_remote_fetch_path_edit)

        layout.addWidget(connection_group)

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

        action_hint = QLabel(
            "Use Tools -> Scan For Printers... to discover hosts, then use Printer + Configuration menus for workflows.",
            tab,
        )
        action_hint.setWordWrap(True)
        layout.addWidget(action_hint)

        ssh_actions_row = QHBoxLayout()
        self.ssh_explore_config_btn = QPushButton("Explore Config Directory", tab)
        self.ssh_explore_config_btn.clicked.connect(self._explore_connected_config_directory)
        ssh_actions_row.addWidget(self.ssh_explore_config_btn)
        ssh_actions_row.addStretch(1)
        layout.addLayout(ssh_actions_row)

        layout.addStretch(1)
        self.ssh_host_edit.textChanged.connect(self._update_action_enablement)
        self.ssh_username_edit.textChanged.connect(self._update_action_enablement)
        self._refresh_saved_connection_profiles()
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

        self.manage_connected_printer_label = QLabel("No active SSH connection.", target_group)
        self.manage_connected_printer_label.setWordWrap(True)
        target_form.addRow("Connected", self.manage_connected_printer_label)

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

        self.manage_refactor_file_btn = QPushButton("Refactor Current .cfg", tab)
        self.manage_refactor_file_btn.clicked.connect(self._manage_refactor_current_file)
        action_row.addWidget(self.manage_refactor_file_btn)

        self.manage_open_control_btn = QPushButton("Open Control In Tab", tab)
        self.manage_open_control_btn.clicked.connect(self._manage_open_control_window)
        action_row.addWidget(self.manage_open_control_btn)

        action_row.addStretch(1)
        layout.addLayout(action_row)
        manage_actions_hint = QLabel(
            "Primary actions run from command bar: Configuration -> Validate Current, "
            "Printer -> Upload Current, Printer -> Restart Klipper.",
            tab,
        )
        manage_actions_hint.setWordWrap(True)
        layout.addWidget(manage_actions_hint)

        editor_splitter = QSplitter(Qt.Horizontal, tab)
        self.manage_file_tree = QTreeWidget(editor_splitter)
        self.manage_file_tree.setHeaderLabels(["Remote Files"])
        self.manage_file_tree.setAlternatingRowColors(True)
        self.manage_file_tree.itemSelectionChanged.connect(self._manage_file_selection_changed)
        self.manage_file_tree.itemDoubleClicked.connect(
            lambda _item, _column: self._manage_open_selected_file()
        )
        self.manage_file_tree.itemExpanded.connect(self._manage_tree_item_expanded)

        editor_panel = QWidget(editor_splitter)
        editor_layout = QVBoxLayout(editor_panel)
        self.manage_current_dir_label = QLabel("Tree root: (not loaded)", editor_panel)
        editor_layout.addWidget(self.manage_current_dir_label)
        self.manage_current_file_label = QLabel("No file loaded.", editor_panel)
        editor_layout.addWidget(self.manage_current_file_label)
        self.manage_file_editor = QPlainTextEdit(editor_panel)
        self.manage_file_editor.textChanged.connect(self._update_action_enablement)
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

        self.ssh_remote_dir_edit.textChanged.connect(self._sync_manage_remote_dir_from_ssh)
        return tab

    def _build_printers_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        if QWebEngineView is None:
            self.printers_embedded_control_view = None
            unavailable = QLabel(
                (
                    "Embedded printer web view is unavailable in this build. "
                    "Use Printer -> Open Control UI (Mainsail/Fluidd)."
                ),
                tab,
            )
            unavailable.setWordWrap(True)
            unavailable.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(unavailable, 1)
        else:
            self.printers_embedded_control_view = QWebEngineView(tab)
            self.printers_embedded_control_view.setUrl(QUrl("about:blank"))
            layout.addWidget(self.printers_embedded_control_view, 1)
        return tab

    def _load_presets(self) -> None:
        try:
            summaries = self.catalog_service.list_presets()
        except PresetCatalogError as exc:
            self._show_error("Preset Load Error", str(exc))
            return

        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("None", None)
        self.presets_by_id.clear()
        for summary in summaries:
            self.preset_combo.addItem(summary.name, summary.id)
            self.presets_by_id[summary.id] = self.catalog_service.load_preset(summary.id)
        self.preset_combo.blockSignals(False)

        if self.preset_combo.count() > 0:
            self.preset_combo.setCurrentIndex(0)
            self._on_preset_changed(0)

    def _on_vendor_changed(self, _: int) -> None:
        vendor = str(self.vendor_combo.currentData() or "")
        if vendor == "voron":
            default_index = self.preset_combo.findData(self.DEFAULT_VORON_PRESET_ID)
            if default_index < 0 and self.preset_combo.count() > 1:
                default_index = 1
            if default_index >= 0 and self.preset_combo.currentIndex() != default_index:
                self.preset_combo.setCurrentIndex(default_index)
            return

        if self.preset_combo.currentIndex() != 0:
            self.preset_combo.setCurrentIndex(0)

    def _on_preset_changed(self, _: int) -> None:
        preset_id = self.preset_combo.currentData()
        if not isinstance(preset_id, str):
            self.current_preset = None
            self.preset_notes_label.setText("")
            self._populate_board_combo([])
            self._populate_toolhead_board_combos([])
            self._populate_probe_types(None)
            self.macros_group.setEnabled(False)
            for checkbox in self.macro_checkboxes.values():
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)
            for checkbox in self.addon_checkboxes.values():
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.setEnabled(False)
                checkbox.blockSignals(False)
            self._sync_toolhead_controls()
            self._sync_led_controls()
            self._refresh_board_summary()
            self._update_machine_attribute_views(None)
            self._update_addon_package_details(None)
            self._render_and_validate()
            return

        preset = self.presets_by_id.get(preset_id)
        if preset is None:
            return

        self.current_preset = preset
        self.preset_notes_label.setText(preset.notes or "")

        available_boards = sorted(set(preset.supported_boards).union(list_main_boards()))
        available_toolheads = sorted(
            set(preset.supported_toolhead_boards).union(list_toolhead_boards())
        )
        self._populate_board_combo(available_boards)
        self._populate_toolhead_board_combos(available_toolheads)
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

        self._sync_toolhead_controls()
        self._sync_led_controls()
        self._refresh_board_summary()
        self._update_addon_package_details()
        self._render_and_validate()

    def _populate_board_combo(self, board_ids: list[str]) -> None:
        current = self.board_combo.currentData()
        self.board_combo.blockSignals(True)
        self.board_combo.clear()
        self.board_combo.addItem("Choose your mainboard", None)
        for board_id in board_ids:
            self.board_combo.addItem(self._format_board_label(board_id), board_id)

        restored = self._set_combo_to_value(self.board_combo, current)
        if not restored and self.board_combo.count() > 0:
            self.board_combo.setCurrentIndex(0)
        self.board_combo.blockSignals(False)

    def _populate_toolhead_board_combos(self, board_ids: list[str]) -> None:
        current_can = self.toolhead_can_board_combo.currentData()
        current_usb = self.toolhead_usb_board_combo.currentData()

        can_ids = sorted(
            (board_id for board_id in board_ids if toolhead_board_transport(board_id) == "can"),
            key=lambda board_id: self._format_toolhead_board_label(board_id).lower(),
        )
        usb_ids = sorted(
            (board_id for board_id in board_ids if toolhead_board_transport(board_id) == "usb"),
            key=lambda board_id: self._format_toolhead_board_label(board_id).lower(),
        )

        self.toolhead_can_board_combo.blockSignals(True)
        self.toolhead_can_board_combo.clear()
        self.toolhead_can_board_combo.addItem("None", None)
        for board_id in can_ids:
            self.toolhead_can_board_combo.addItem(self._format_toolhead_board_label(board_id), board_id)
        self._set_combo_to_value(self.toolhead_can_board_combo, current_can)
        self.toolhead_can_board_combo.blockSignals(False)

        self.toolhead_usb_board_combo.blockSignals(True)
        self.toolhead_usb_board_combo.clear()
        self.toolhead_usb_board_combo.addItem("None", None)
        for board_id in usb_ids:
            self.toolhead_usb_board_combo.addItem(self._format_toolhead_board_label(board_id), board_id)
        self._set_combo_to_value(self.toolhead_usb_board_combo, current_usb)
        self.toolhead_usb_board_combo.blockSignals(False)

    def _selected_toolhead_board(self) -> tuple[str | None, str | None]:
        can_board = self.toolhead_can_board_combo.currentData()
        if isinstance(can_board, str):
            return can_board, "can"
        usb_board = self.toolhead_usb_board_combo.currentData()
        if isinstance(usb_board, str):
            return usb_board, "usb"
        return None, None

    def _on_toolhead_can_board_changed(self, _: int) -> None:
        if isinstance(self.toolhead_can_board_combo.currentData(), str):
            self.toolhead_usb_board_combo.blockSignals(True)
            self.toolhead_usb_board_combo.setCurrentIndex(0)
            self.toolhead_usb_board_combo.blockSignals(False)
        self._sync_toolhead_controls()
        self._render_and_validate()

    def _on_toolhead_usb_board_changed(self, _: int) -> None:
        if isinstance(self.toolhead_usb_board_combo.currentData(), str):
            self.toolhead_can_board_combo.blockSignals(True)
            self.toolhead_can_board_combo.setCurrentIndex(0)
            self.toolhead_can_board_combo.blockSignals(False)
        self._sync_toolhead_controls()
        self._render_and_validate()

    def _populate_probe_types(self, preset: Preset | None) -> None:
        current = self.probe_type_combo.currentText().strip()
        probe_types = list(self.DEFAULT_PROBE_TYPES)
        if preset is not None:
            probe_types = list(
                dict.fromkeys([*preset.recommended_probe_types, *self.DEFAULT_PROBE_TYPES])
            )
        self.probe_type_combo.blockSignals(True)
        self.probe_type_combo.clear()
        self.probe_type_combo.addItem("None")
        for probe_type in probe_types:
            self.probe_type_combo.addItem(probe_type)
        if current and current.lower() != "none":
            self.probe_type_combo.setCurrentText(current)
        else:
            self.probe_type_combo.setCurrentIndex(0)
        self.probe_type_combo.blockSignals(False)

    def _apply_addon_support(self, preset: Preset) -> None:
        _ = preset
        for checkbox in self.addon_checkboxes.values():
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.setEnabled(False)
            checkbox.blockSignals(False)

    def _add_addon_checkbox(self, addon_name: str) -> None:
        if addon_name in self.addon_checkboxes:
            return
        if not hasattr(self, "addons_layout"):
            return

        checkbox = QCheckBox(addon_name, self.addons_group)
        checkbox.setObjectName(f"addon_{addon_name}")
        checkbox.toggled.connect(
            lambda checked, name=addon_name: self._on_addon_checkbox_toggled(name, checked)
        )
        self.addons_layout.addWidget(checkbox)
        self.addon_checkboxes[addon_name] = checkbox

    def _on_macro_checkbox_toggled(self, _macro_name: str, _checked: bool) -> None:
        self._render_and_validate()

    def _on_addon_checkbox_toggled(self, _addon_name: str, _checked: bool) -> None:
        self._update_addon_package_details()
        self._render_and_validate()

    def _sync_toolhead_controls(self) -> None:
        selected_board, transport = self._selected_toolhead_board()
        self.toolhead_canbus_uuid_edit.setEnabled(selected_board is not None and transport == "can")

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
        return []

    @staticmethod
    def _format_section_value_map(section_map: dict[str, dict[str, str]]) -> str:
        if not section_map:
            return "None detected."
        lines: list[str] = []
        for section_name, values in section_map.items():
            lines.append(f"[{section_name}]")
            if not values:
                lines.append("  (no key/value entries)")
                continue
            for key, value in values.items():
                rendered = str(value)
                rendered = rendered.replace("\n", "\\n")
                lines.append(f"  {key}: {rendered}")
        return "\n".join(lines)

    def _update_machine_attribute_views(self, project: ProjectConfig | None = None) -> None:
        if not hasattr(self, "machine_attr_mcu_view"):
            return
        active_project = project or self.current_project
        if active_project is None:
            self.machine_attr_mcu_view.setPlainText("No machine attributes available.")
            self.machine_attr_motion_view.setPlainText("No machine attributes available.")
            self.machine_attr_probe_view.setPlainText("No machine attributes available.")
            self.machine_attr_thermal_view.setPlainText("No machine attributes available.")
            return

        attributes = active_project.machine_attributes
        mcu_lines: list[str] = []
        for mcu_name, mcu in attributes.mcu_map.items():
            mcu_lines.append(f"[mcu {mcu_name}]")
            mcu_lines.append(f"  serial: {mcu.serial or 'None'}")
            mcu_lines.append(f"  canbus_uuid: {mcu.canbus_uuid or 'None'}")
            mcu_lines.append(f"  restart_method: {mcu.restart_method or 'None'}")
        if not mcu_lines:
            mcu_lines.append("No MCU map entries detected.")
        self.machine_attr_mcu_view.setPlainText("\n".join(mcu_lines))

        motion_sections = dict(attributes.stepper_sections)
        motion_sections.update(attributes.driver_sections)
        self.machine_attr_motion_view.setPlainText(self._format_section_value_map(motion_sections))

        probe_sections = dict(attributes.probe_sections)
        probe_sections.update(attributes.leveling_sections)
        self.machine_attr_probe_view.setPlainText(self._format_section_value_map(probe_sections))

        thermal_sections = dict(attributes.thermal_sections)
        thermal_sections.update(attributes.fan_sections)
        thermal_sections.update(attributes.sensor_sections)
        thermal_sections.update(attributes.resonance_sections)
        self.machine_attr_thermal_view.setPlainText(self._format_section_value_map(thermal_sections))

    def _update_addon_package_details(self, project: ProjectConfig | None = None) -> None:
        if not hasattr(self, "addon_package_details_view"):
            return
        _ = project
        self.addon_package_details_view.setPlainText(
            "Add-on support is temporarily disabled in this build."
        )

    def _build_project_from_ui(self) -> ProjectConfig:
        if self.current_preset is None:
            raise ValueError("No preset selected.")

        board_id = self.board_combo.currentData()
        if not isinstance(board_id, str):
            board_id = ""

        raw_probe_type = self.probe_type_combo.currentText().strip()
        probe_type = raw_probe_type if raw_probe_type and raw_probe_type.lower() != "none" else None
        probe_enabled = probe_type is not None

        toolhead_board, toolhead_transport = self._selected_toolhead_board()
        toolhead_enabled = toolhead_board is not None

        toolhead_uuid = self.toolhead_canbus_uuid_edit.text().strip() or None
        if not toolhead_enabled or toolhead_transport != "can":
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
                "type": probe_type,
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
        self.action_log_service.log_event(
            "generate",
            phase="start",
            preset_id=self.current_preset.id,
        )

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
            self._update_machine_attribute_views(None)
            self._update_addon_package_details(None)
            self.action_log_service.log_event(
                "generate",
                phase="failed",
                reason="project_build_failed",
                blocking=sum(1 for finding in report.findings if finding.severity == "blocking"),
            )
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
            self._update_machine_attribute_views(None)
            self._update_addon_package_details(None)
            self.action_log_service.log_event(
                "generate",
                phase="failed",
                reason="preset_not_found",
                preset_id=project.preset_id,
            )
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

        if (
            pack is not None
            and self.current_import_profile is not None
            and self.imported_file_map
        ):
            parity_report = self.parity_service.compare(
                pack,
                self.imported_file_map,
                imported_root_file=self.current_import_profile.root_file,
                imported_include_graph=self.current_import_profile.include_graph,
            )
            combined.findings.extend(parity_report.findings)

        self.current_project = project
        self.current_pack = pack
        self.current_report = combined

        self._update_validation_view(combined)
        self._update_generated_files_view(pack)
        self._update_action_enablement()
        self._refresh_board_summary()
        self._update_machine_attribute_views(project)
        self._update_addon_package_details(project)
        blocking_count = sum(1 for finding in combined.findings if finding.severity == "blocking")
        warning_count = sum(1 for finding in combined.findings if finding.severity == "warning")
        self.app_state_store.update_validation(
            blocking=blocking_count,
            warnings=warning_count,
            source_label="compile",
        )
        self.action_log_service.log_event(
            "generate",
            phase="complete",
            preset_id=project.preset_id,
            file_count=(len(pack.files) if pack is not None else 0),
            blocking=blocking_count,
            warnings=warning_count,
        )
        self.statusBar().showMessage("Compile complete", 2500)

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

        if blocking_count > 0:
            self._last_warning_toast_snapshot = (blocking_count, warning_count)
            return

        snapshot = (blocking_count, warning_count)
        if warning_count <= 0:
            self._last_warning_toast_snapshot = snapshot
            return

        if snapshot == self._last_warning_toast_snapshot:
            return

        plural = "warning" if warning_count == 1 else "warnings"
        self._show_toast_notification(
            (
                f"Heads up: we found {warning_count} {plural}. "
                "You can keep going, but open Validation Findings to review them."
            ),
            severity="caution",
        )
        self._last_warning_toast_snapshot = snapshot

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
            return

        message_parts = [
            f"{finding.code}: {finding.message}"
            for finding in blocking_findings[:2]
        ]
        if len(blocking_findings) > 2:
            message_parts.append(f"+{len(blocking_findings) - 2} more")

        summary = " | ".join(message_parts)

        snapshot = tuple(
            f"{finding.code}|{finding.field or ''}|{finding.message}"
            for finding in blocking_findings
        )
        if snapshot != self._last_blocking_alert_snapshot:
            self.statusBar().showMessage(f"Blocking conflicts: {len(blocking_findings)}", 3000)
            plural = "issue" if len(blocking_findings) == 1 else "issues"
            self._show_toast_notification(
                (
                    f"Please fix {len(blocking_findings)} critical {plural} before you deploy. "
                    f"Top item: {summary}"
                ),
                severity="warning",
                duration_ms=5500,
            )
        self._last_blocking_alert_snapshot = snapshot

    def _on_wizard_package_file_selected(self) -> None:
        self._update_wizard_package_preview()

    def _update_wizard_package_view(self, pack: RenderedPack | None) -> None:
        if not hasattr(self, "wizard_package_file_list"):
            return

        current_name = ""
        current_item = self.wizard_package_file_list.currentItem()
        if current_item is not None:
            current_name = current_item.text().strip()

        self.wizard_package_file_list.blockSignals(True)
        self.wizard_package_file_list.clear()
        if pack is not None:
            for name in pack.files.keys():
                self.wizard_package_file_list.addItem(name)
        self.wizard_package_file_list.blockSignals(False)

        if self.wizard_package_file_list.count() <= 0:
            if hasattr(self, "wizard_package_preview_label"):
                self.wizard_package_preview_label.setText("No generated files.")
            if hasattr(self, "wizard_package_preview"):
                self.wizard_package_preview.clear()
                self.wizard_package_preview.setPlainText(
                    "Compile to generate package files and preview their contents."
                )
            return

        target_row = 0
        if current_name:
            for row in range(self.wizard_package_file_list.count()):
                item = self.wizard_package_file_list.item(row)
                if item is not None and item.text() == current_name:
                    target_row = row
                    break
        self.wizard_package_file_list.setCurrentRow(target_row)
        self._update_wizard_package_preview()

    def _update_wizard_package_preview(self) -> None:
        if not hasattr(self, "wizard_package_preview"):
            return
        if self.current_pack is None:
            self.wizard_package_preview_label.setText("No generated files.")
            self.wizard_package_preview.clear()
            return

        item = self.wizard_package_file_list.currentItem()
        if item is None:
            self.wizard_package_preview_label.setText("No generated files selected.")
            self.wizard_package_preview.clear()
            return

        file_name = item.text()
        content = self.current_pack.files.get(file_name, "")
        self.wizard_package_preview_label.setText(f"Generated: {file_name}")
        self.wizard_package_preview.setPlainText(content)

    def _update_generated_files_view(self, pack: RenderedPack | None) -> None:
        self._update_wizard_package_view(pack)

        if pack is not None and "printer.cfg" in pack.files:
            self._set_persistent_preview_source(
                content=pack.files["printer.cfg"],
                label="Generated: printer.cfg",
                source_kind="generated",
                generated_name="printer.cfg",
                source_key="generated:printer.cfg",
                update_last=False,
            )
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
        can_validate = self._can_validate_current_context()
        can_upload_generated = self._can_upload_generated_pack()
        can_upload_current = self.device_connected and self._can_upload_current_context()
        has_ssh_target = self._has_ssh_target_configured()
        can_restart = self.device_connected and has_ssh_target

        if hasattr(self, "export_folder_action"):
            self.export_folder_action.setEnabled(can_output)
        if hasattr(self, "export_zip_action"):
            self.export_zip_action.setEnabled(can_output)
        if hasattr(self, "file_export_generated_pack_action"):
            self.file_export_generated_pack_action.setEnabled(can_output)
        if hasattr(self, "tools_deploy_action"):
            self.tools_deploy_action.setEnabled(can_upload_generated)
        if hasattr(self, "configuration_validate_action"):
            self.configuration_validate_action.setEnabled(can_validate)
        if hasattr(self, "printer_upload_action"):
            self.printer_upload_action.setEnabled(can_upload_current)
        if hasattr(self, "printer_connect_action"):
            self.printer_connect_action.setEnabled(has_ssh_target and not self.auto_connect_in_progress)
        if hasattr(self, "printer_disconnect_action"):
            self.printer_disconnect_action.setEnabled(self.device_connected)
        if hasattr(self, "tools_connect_action"):
            self.tools_connect_action.setEnabled(
                has_ssh_target and not self.auto_connect_in_progress
            )
        if hasattr(self, "printer_restart_klipper_action"):
            self.printer_restart_klipper_action.setEnabled(can_restart)
        if hasattr(self, "printer_restart_host_action"):
            self.printer_restart_host_action.setEnabled(can_restart)

    def _on_generated_file_selected(self) -> None:
        item = self.generated_file_list.currentItem() if hasattr(self, "generated_file_list") else None
        if item is not None:
            self.action_log_service.log_event(
                "files_select",
                item=item.text(),
                imported=bool(self._showing_external_file),
            )
        if self._showing_external_file and self.imported_file_map:
            self._show_selected_imported_file()
            return
        if self._showing_external_file:
            return
        self._show_selected_generated_file()

    def _show_selected_generated_file(self) -> None:
        if self._showing_external_file:
            self._showing_external_file = False
            self.imported_file_order = []
            self.generated_file_list.clear()
            self._update_generated_files_view(self.current_pack)
            return

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
        previous_label = self.files_current_label
        previous_source = self.files_current_source
        self.files_current_content = content
        self.files_current_label = label
        self.files_current_source = source
        self.files_current_generated_name = generated_name
        self.app_state_store.update_active_file(path=label, source=source, dirty=False)
        self.file_preview.setPlainText(content)
        self.preview_path_label.setText(label)
        if hasattr(self, "files_breadcrumbs_label"):
            self.files_breadcrumbs_label.setText(
                f"Path: {self._format_breadcrumbs_label(label, generated_name)}"
            )
        self._set_persistent_preview_source(
            content=content,
            label=label,
            source_kind=source,
            generated_name=generated_name,
            update_last=True,
        )
        self._rebuild_cfg_form()
        if self._is_cfg_label(label, generated_name):
            self._run_current_cfg_validation(show_dialog=False)
        else:
            self._clear_cfg_tools_status()
        if label != previous_label or source != previous_source:
            self.action_log_service.log_event(
                "files_open",
                source=source,
                label=label,
                generated_name=generated_name or "",
            )

    @staticmethod
    def _format_breadcrumbs_label(label: str, generated_name: str | None) -> str:
        if generated_name:
            return "Generated > " + generated_name.replace("\\", " / ").replace("/", " > ")
        clean = str(label or "").replace("\\", "/")
        clean = clean.replace("Generated: ", "")
        clean = clean.replace("Remote: ", "")
        clean = clean.replace("Local: ", "")
        clean = clean.strip()
        if not clean:
            return "none"
        return clean.replace("/", " > ")

    def _show_generated_files_context_menu(self, position) -> None:  # noqa: ANN001
        item = self.generated_file_list.itemAt(position)
        if item is None:
            return

        menu = QMenu(self.generated_file_list)
        open_action = menu.addAction("Open")
        validate_action = menu.addAction("Validate Current")
        refactor_action = menu.addAction("Refactor Current")
        copy_path_action = menu.addAction("Copy Path")
        action = menu.exec(self.generated_file_list.mapToGlobal(position))
        if action is None:
            return

        self.generated_file_list.setCurrentItem(item)
        self._show_selected_generated_file()

        if action is open_action:
            return
        if action is validate_action:
            self._validate_current_cfg_file()
            return
        if action is refactor_action:
            self._refactor_current_cfg_file()
            return
        if action is copy_path_action:
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(item.text())
            self.statusBar().showMessage("Copied file path", 1500)

    def _current_cfg_target_label(self) -> str:
        if self.files_current_generated_name:
            return self.files_current_generated_name
        label = self.files_current_label.strip()
        if not label:
            return "current.cfg"
        return Path(label).name or label

    def _current_cfg_context(self, show_error: bool = True) -> tuple[str, str] | None:
        if not self.files_current_content.strip():
            if show_error:
                self._show_error("Files", "No file is loaded.")
            return None
        if not self._is_cfg_label(self.files_current_label, self.files_current_generated_name):
            if show_error:
                self._show_error("Files", "Current file is not a .cfg file.")
            return None
        return self.files_current_content, self._current_cfg_target_label()

    def _clear_cfg_tools_status(self) -> None:
        self.current_cfg_report = ValidationReport()
        self.cfg_tools_status_label.clear()
        self.cfg_tools_status_label.setStyleSheet("")
        self.cfg_tools_status_label.setVisible(False)
        self._update_files_experiment_chips(blocking=0, warnings=0, source_label="")

    def _update_cfg_tools_status(self, report: ValidationReport, source_label: str) -> None:
        blocking = sum(1 for finding in report.findings if finding.severity == "blocking")
        warnings = sum(1 for finding in report.findings if finding.severity == "warning")
        total = blocking + warnings

        if self._is_files_experiment_enabled():
            if total == 0:
                message = f"Looks good: {source_label} has no validation issues."
                style = (
                    "QLabel {"
                    " background-color: #14532d;"
                    " color: #ffffff;"
                    " border: 1px solid #16a34a;"
                    " border-radius: 8px;"
                    " padding: 8px 10px;"
                    " font-weight: 600;"
                    "}"
                )
            elif blocking > 0:
                plural_blocking = "issue" if blocking == 1 else "issues"
                plural_warning = "warning" if warnings == 1 else "warnings"
                message = (
                    f"Action needed: fix {blocking} critical {plural_blocking} before upload. "
                    f"We also found {warnings} {plural_warning}."
                )
                style = (
                    "QLabel {"
                    " background-color: #7f1d1d;"
                    " color: #ffffff;"
                    " border: 1px solid #ef4444;"
                    " border-radius: 8px;"
                    " padding: 8px 10px;"
                    " font-weight: 600;"
                    "}"
                )
            else:
                plural_warning = "warning" if warnings == 1 else "warnings"
                message = (
                    f"Heads up: we found {warnings} {plural_warning}. "
                    "You can keep editing, but review them before upload."
                )
                style = (
                    "QLabel {"
                    " background-color: #78350f;"
                    " color: #ffffff;"
                    " border: 1px solid #f59e0b;"
                    " border-radius: 8px;"
                    " padding: 8px 10px;"
                    " font-weight: 600;"
                    "}"
                )
        else:
            if total == 0:
                message = f"{source_label}: no firmware validation issues."
                style = (
                    "QLabel {"
                    " background-color: #14532d;"
                    " color: #ffffff;"
                    " border: 1px solid #16a34a;"
                    " border-radius: 4px;"
                    " padding: 6px 8px;"
                    " font-weight: 600;"
                    "}"
                )
            elif blocking > 0:
                message = (
                    f"{source_label}: {blocking} blocking and {warnings} warning issue(s) found."
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
                message = f"{source_label}: warnings only ({warnings})."
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

        self.cfg_tools_status_label.setText(message)
        self.cfg_tools_status_label.setStyleSheet(style)
        self.cfg_tools_status_label.setVisible(True)
        self._update_files_experiment_chips(
            blocking=blocking,
            warnings=warnings,
            source_label=source_label,
        )

    @staticmethod
    def _build_cfg_validation_details(report: ValidationReport, limit: int = 8) -> str:
        if not report.findings:
            return "No issues detected."
        lines: list[str] = []
        for finding in report.findings[:limit]:
            field_suffix = f" ({finding.field})" if finding.field else ""
            lines.append(
                f"[{finding.severity}] {finding.code}{field_suffix}: {finding.message}"
            )
        hidden = len(report.findings) - len(lines)
        if hidden > 0:
            lines.append(f"... {hidden} additional finding(s) omitted.")
        return "\n".join(lines)

    def _run_current_cfg_validation(self, show_dialog: bool) -> ValidationReport | None:
        self.app_state_store.update_ui(right_panel_mode="validation")
        context = self._current_cfg_context(show_error=show_dialog)
        if context is None:
            return None
        content, source_label = context

        if (
            self.files_current_source == "imported"
            and self.current_import_profile is not None
            and self.imported_file_map
        ):
            report = self.firmware_tools_service.validate_graph(
                self.imported_file_map,
                self.current_import_profile.root_file,
            )
            status_source_label = f"Imported graph ({self.current_import_profile.root_file})"
        else:
            report = self.firmware_tools_service.validate_cfg(
                content,
                source_label=source_label,
                role="auto",
            )
            status_source_label = source_label
        self.current_cfg_report = report
        self._update_cfg_tools_status(report, source_label=status_source_label)
        preview_key = self._build_preview_source_key(
            self.files_current_source,
            self.files_current_label,
            self.files_current_generated_name,
        )

        blocking = sum(1 for finding in report.findings if finding.severity == "blocking")
        warnings = sum(1 for finding in report.findings if finding.severity == "warning")
        self.app_state_store.update_validation(
            blocking=blocking,
            warnings=warnings,
            source_label=status_source_label,
        )
        self.action_log_service.log_event(
            "validate",
            source=status_source_label,
            blocking=blocking,
            warnings=warnings,
            triggered_by="dialog" if show_dialog else "background",
        )
        self.action_log_service.log_event(
            "files_validate",
            source=status_source_label,
            blocking=blocking,
            warnings=warnings,
            triggered_by="dialog" if show_dialog else "background",
        )
        self._set_preview_validation_state(
            preview_key,
            blocking=blocking,
            warnings=warnings,
        )
        if blocking > 0:
            self.statusBar().showMessage(f"Firmware validation: {blocking} blocking issue(s)", 3500)
        elif warnings > 0:
            self.statusBar().showMessage(f"Firmware validation: {warnings} warning issue(s)", 3500)
        else:
            self.statusBar().showMessage("Firmware validation passed", 2500)

        if show_dialog:
            details = self._build_cfg_validation_details(report)
            if blocking > 0:
                QMessageBox.critical(
                    self,
                    "Firmware Validation",
                    f"{status_source_label}: {blocking} blocking, {warnings} warning.\n\n{details}",
                )
            elif warnings > 0:
                QMessageBox.warning(
                    self,
                    "Firmware Validation",
                    f"{status_source_label}: warnings detected ({warnings}).\n\n{details}",
                )
            else:
                QMessageBox.information(
                    self,
                    "Firmware Validation",
                    f"{status_source_label}: no issues detected.",
                )
        return report

    def _validate_current_cfg_file(self, _checked: bool = False) -> None:
        self._run_current_cfg_validation(show_dialog=True)

    def _refactor_current_cfg_file(self, _checked: bool = False) -> None:
        context = self._current_cfg_context(show_error=True)
        if context is None:
            return
        content, source_label = context

        updated, changes = self.firmware_tools_service.refactor_cfg(content)
        if updated != content:
            self._set_files_tab_content(
                content=updated,
                label=self.files_current_label,
                source=self.files_current_source,
                generated_name=self.files_current_generated_name,
            )
            self.app_state_store.update_active_file(
                path=self.files_current_label,
                source=self.files_current_source,
                dirty=True,
            )
            self._update_files_experiment_chips(
                blocking=sum(
                    1 for finding in self.current_cfg_report.findings if finding.severity == "blocking"
                ),
                warnings=sum(
                    1 for finding in self.current_cfg_report.findings if finding.severity == "warning"
                ),
                source_label=self._current_cfg_target_label(),
            )
            if (
                self.files_current_source == "generated"
                and self.current_pack is not None
                and self.files_current_generated_name
            ):
                self.current_pack.files[self.files_current_generated_name] = updated
            self.statusBar().showMessage(f"Refactored {source_label} ({changes} change(s))", 3000)
        else:
            self.statusBar().showMessage(f"No refactor changes for {source_label}", 2500)
        self.action_log_service.log_event(
            "files_refactor",
            source=source_label,
            changes=int(changes),
        )
        self._run_current_cfg_validation(show_dialog=False)

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

        self.app_state_store.update_active_file(
            path=self.files_current_label,
            source=self.files_current_source,
            dirty=True,
        )
        self.action_log_service.log_event(
            "files_apply_form",
            source=self.files_current_label,
            field_count=len(self.cfg_form_editors),
        )
        self._update_files_experiment_chips(
            blocking=sum(1 for finding in self.current_cfg_report.findings if finding.severity == "blocking"),
            warnings=sum(1 for finding in self.current_cfg_report.findings if finding.severity == "warning"),
            source_label=self._current_cfg_target_label(),
        )
        self.statusBar().showMessage("Applied form changes to current file view", 2500)
        self._run_current_cfg_validation(show_dialog=False)

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

        if hasattr(self, "export_status_label"):
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

        if hasattr(self, "export_status_label"):
            self.export_status_label.setText(f"ZIP export complete: {zip_path}")
        self.statusBar().showMessage("Exported zip", 2500)
    def _save_project_to_file(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project",
            self.current_project_path or str(Path.home() / "klippconfig-project.json"),
            "KlippConfig project (*.json)",
        )
        if not file_path:
            return

        self._save_project_to_path(file_path)

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
        self.current_project_path = file_path
        self._render_and_validate()
        self.statusBar().showMessage(f"Loaded project: {file_path}", 2500)

    def _new_project(self) -> None:
        if self.preset_combo.count() == 0:
            return
        self._applying_project = False
        self.vendor_combo.setCurrentIndex(0)
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
        self.current_project_path = None
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

            self.probe_type_combo.setCurrentText(project.probe.type or "None")

            self.hotend_thermistor_edit.setText(project.thermistors.hotend)
            self.bed_thermistor_edit.setText(project.thermistors.bed)

            self.toolhead_can_board_combo.blockSignals(True)
            self.toolhead_usb_board_combo.blockSignals(True)
            self.toolhead_can_board_combo.setCurrentIndex(0)
            self.toolhead_usb_board_combo.setCurrentIndex(0)
            if project.toolhead.board:
                if toolhead_board_transport(project.toolhead.board) == "usb":
                    toolhead_index = self.toolhead_usb_board_combo.findData(project.toolhead.board)
                    if toolhead_index >= 0:
                        self.toolhead_usb_board_combo.setCurrentIndex(toolhead_index)
                else:
                    toolhead_index = self.toolhead_can_board_combo.findData(project.toolhead.board)
                    if toolhead_index >= 0:
                        self.toolhead_can_board_combo.setCurrentIndex(toolhead_index)
            self.toolhead_can_board_combo.blockSignals(False)
            self.toolhead_usb_board_combo.blockSignals(False)
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
                checkbox.setChecked(False)

            self._replace_overrides(project.advanced_overrides)
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
        if not hasattr(self, "board_summary"):
            return

        board_id = self.board_combo.currentData()
        toolhead_id, _ = self._selected_toolhead_board()
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

        if isinstance(toolhead_id, str):
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

    def _ensure_printer_discovery_window(self) -> PrinterDiscoveryWindow:
        if self.printer_discovery_window is not None:
            return self.printer_discovery_window

        suggested_cidrs = self.discovery_service.suggest_scan_cidrs()
        discovery_window = PrinterDiscoveryWindow(suggested_cidrs=suggested_cidrs, parent=self)
        discovery_window.scan_network_btn.clicked.connect(self._scan_for_printers)
        discovery_window.use_selected_host_btn.clicked.connect(self._use_selected_discovery_host)
        discovery_window.discovery_results_table.cellDoubleClicked.connect(
            lambda _row, _col: self._use_selected_discovery_host()
        )

        self.printer_discovery_window = discovery_window
        self.scan_cidr_edit = discovery_window.scan_cidr_edit
        self.scan_timeout_spin = discovery_window.scan_timeout_spin
        self.scan_max_hosts_spin = discovery_window.scan_max_hosts_spin
        self.discovery_results_table = discovery_window.discovery_results_table
        self.scan_network_btn = discovery_window.scan_network_btn
        return discovery_window

    def _scan_for_printers(self) -> None:
        self._ensure_printer_discovery_window()
        cidr = self.scan_cidr_edit.text().strip()
        timeout = float(self.scan_timeout_spin.value())
        max_hosts = int(self.scan_max_hosts_spin.value())

        if hasattr(self, "scan_network_btn"):
            self.scan_network_btn.setEnabled(False)
        if hasattr(self, "tools_scan_printers_action"):
            self.tools_scan_printers_action.setEnabled(False)
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
            if hasattr(self, "scan_network_btn"):
                self.scan_network_btn.setEnabled(True)
            if hasattr(self, "tools_scan_printers_action"):
                self.tools_scan_printers_action.setEnabled(True)

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
        self._ensure_printer_discovery_window()
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
        self._refresh_modify_connection_summary()
        if not self.ssh_connection_name_edit.text().strip():
            self.ssh_connection_name_edit.setText(str(host))
        self._append_ssh_log(f"Using discovered host: {host}")
        self._append_manage_log(f"Using discovered host: {host}")
        self._append_modify_log(f"Using discovered host: {host}")
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

    def _explore_connected_config_directory(self) -> None:
        if not self.device_connected:
            self._show_error(
                "Printer Connection",
                "Connect to a printer first, then explore its config directory.",
            )
            return

        host = self.ssh_host_edit.text().strip()
        if not host:
            self._show_error("Printer Connection", "SSH host is empty.")
            return

        remote_dir = self.ssh_remote_dir_edit.text().strip() or "~/printer_data/config"
        self.manage_host_edit.setText(host)
        self.manage_remote_dir_edit.setText(remote_dir)
        self.tabs.setCurrentWidget(self.manage_printer_tab)
        self._append_ssh_log(f"Exploring config directory: {host} -> {remote_dir}")
        self._append_manage_log(f"Exploring config directory: {host} -> {remote_dir}")
        self._manage_refresh_files(target_dir=remote_dir)
        self.statusBar().showMessage("Opened connected printer config explorer", 3000)

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
        source = manual_url or self.ssh_host_edit.text().strip() or self._resolve_manage_host()
        normalized = self._normalize_control_url(source)
        if normalized:
            return normalized
        if manual_url:
            self._show_error(
                "Printers",
                "Control URL is invalid. Example: http://192.168.1.20/ or printer.local.",
            )
        else:
            self._show_error("Printers", "Set a host in the Printer Connection window.")
        return ""

    def _manage_open_control_window(self) -> None:
        control_url = self._resolve_manage_control_url()
        if not control_url:
            return

        self.tabs.setCurrentWidget(self.printers_tab)
        embedded_view = getattr(self, "printers_embedded_control_view", None)
        if embedded_view is not None:
            try:
                embedded_view.setUrl(QUrl(control_url))
            except RuntimeError as exc:
                opened = QDesktopServices.openUrl(QUrl(control_url))
                if opened:
                    self._append_manage_log(f"{exc} Opened in external browser: {control_url}")
                    self.statusBar().showMessage("Embedded view unavailable; opened browser", 3500)
                    self.app_state_store.update_ui(active_route="printers", right_panel_mode="context")
                    return
                self._show_error("Manage Printer", str(exc))
                return
            self._append_manage_log(f"Opened control view in tab: {control_url}")
            self.statusBar().showMessage(f"Control view opened: {control_url}", 3000)
            self.app_state_store.update_ui(active_route="printers", right_panel_mode="context")
            return

        opened = QDesktopServices.openUrl(QUrl(control_url))
        if opened:
            self._append_manage_log(
                f"Embedded view unavailable. Opened in external browser: {control_url}"
            )
            self.statusBar().showMessage("Embedded view unavailable; opened browser", 3500)
            self.app_state_store.update_ui(active_route="printers", right_panel_mode="context")
            return

        self._show_error("Printers", "Unable to open embedded or external control view.")

    def _manage_reload_embedded_control_view(self) -> None:
        embedded_view = getattr(self, "printers_embedded_control_view", None)
        if embedded_view is None:
            self._manage_open_control_window()
            return
        try:
            embedded_view.reload()
        except RuntimeError as exc:
            self._show_error("Manage Printer", str(exc))
            return
        self.statusBar().showMessage("Control view reloaded", 2500)

    def _manage_open_control_external(self) -> None:
        control_url = self._resolve_manage_control_url()
        if not control_url:
            return
        if QDesktopServices.openUrl(QUrl(control_url)):
            self._append_manage_log(f"Opened control URL in browser: {control_url}")
            self.statusBar().showMessage("Opened control URL in browser", 2500)
            return
        self._show_error("Manage Printer", "Could not open external browser for control URL.")

    def _collect_manage_params(self) -> dict[str, Any] | None:
        host = self._resolve_manage_host()
        if not host:
            self._show_error("Manage Printer", "Set a host in SSH or Manage Printer tab.")
            return None
        return self._collect_ssh_params(host_override=host)

    def _append_manage_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        self.manage_log.appendPlainText(line)
        if hasattr(self, "console_activity_log"):
            self.console_activity_log.appendPlainText(f"[MANAGE] {line}")

    def _manage_resolve_root_directory(self) -> str:
        return self.manage_remote_dir_edit.text().strip() or self.ssh_remote_dir_edit.text().strip()

    @staticmethod
    def _manage_parent_directory(path: str) -> str:
        normalized = path.rstrip("/") or "/"
        parent = posixpath.dirname(normalized) or "/"
        return parent

    @staticmethod
    def _manage_tree_path_role() -> int:
        return int(Qt.ItemDataRole.UserRole)

    @staticmethod
    def _manage_tree_type_role() -> int:
        return int(Qt.ItemDataRole.UserRole + 1)

    @staticmethod
    def _manage_tree_loaded_role() -> int:
        return int(Qt.ItemDataRole.UserRole + 2)

    def _manage_selected_tree_item(self) -> QTreeWidgetItem | None:
        selected = self.manage_file_tree.selectedItems()
        if not selected:
            return None
        return selected[0]

    @staticmethod
    def _manage_entry_sort_key(entry: dict[str, Any]) -> tuple[int, str]:
        entry_type = str(entry.get("type") or "file")
        name = str(entry.get("name") or "")
        return (0 if entry_type == "dir" else 1, name.casefold())

    def _manage_create_tree_item(
        self,
        *,
        name: str,
        remote_path: str,
        entry_type: str,
        loaded: bool,
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem([name])
        item.setData(0, self._manage_tree_path_role(), remote_path)
        item.setData(0, self._manage_tree_type_role(), entry_type)
        item.setData(0, self._manage_tree_loaded_role(), loaded)
        if entry_type == "dir":
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        return item

    def _manage_populate_tree_children(
        self,
        parent_item: QTreeWidgetItem,
        entries: list[dict[str, Any]],
    ) -> int:
        count = 0
        for entry in sorted(entries, key=self._manage_entry_sort_key):
            entry_type = str(entry.get("type") or "file")
            name = str(entry.get("name") or "").strip()
            remote_path = str(entry.get("path") or "").strip()
            if not name or not remote_path:
                continue
            item = self._manage_create_tree_item(
                name=name,
                remote_path=remote_path,
                entry_type=entry_type,
                loaded=(entry_type != "dir"),
            )
            parent_item.addChild(item)
            count += 1
        return count

    def _manage_load_tree_item(
        self,
        item: QTreeWidgetItem,
        service: SSHDeployService | None = None,
        params: dict[str, Any] | None = None,
    ) -> bool:
        entry_type = str(item.data(0, self._manage_tree_type_role()) or "file")
        remote_path = str(item.data(0, self._manage_tree_path_role()) or "").strip()
        if entry_type != "dir" or not remote_path:
            return False
        if bool(item.data(0, self._manage_tree_loaded_role())):
            return True

        local_service = service or self._get_ssh_service()
        if local_service is None:
            return False
        local_params = params or self._collect_manage_params()
        if local_params is None:
            return False

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            listing = local_service.list_directory(remote_dir=remote_path, **local_params)
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._show_error("Manage Printer", str(exc))
            self._append_manage_log(f"Folder load failed: {exc}")
            return False
        finally:
            QApplication.restoreOverrideCursor()

        entries = list(listing.get("entries") or [])
        item.takeChildren()
        shown_count = self._manage_populate_tree_children(item, entries)
        item.setData(0, self._manage_tree_loaded_role(), True)
        item.setExpanded(True)
        self.manage_current_directory = remote_path
        self.manage_remote_dir_edit.setText(remote_path)
        self.manage_current_dir_label.setText(f"Tree root: {self.manage_remote_dir_edit.text().strip()}")
        self._append_manage_log(f"Loaded {shown_count} entries from {remote_path}.")
        self._set_device_connection_health(True, f"Host {local_params['host']} reachable.")
        return True

    def _manage_tree_item_expanded(self, item: QTreeWidgetItem) -> None:
        entry_type = str(item.data(0, self._manage_tree_type_role()) or "file")
        if entry_type != "dir":
            return
        if bool(item.data(0, self._manage_tree_loaded_role())):
            return
        self._manage_load_tree_item(item)

    def _manage_tree_root_display_name(self, remote_dir: str) -> str:
        normalized = remote_dir.rstrip("/") or "/"
        if normalized == "/":
            return "/"
        name = posixpath.basename(normalized)
        if name:
            return name
        return normalized

    def _manage_browse_up_directory(self) -> None:
        selected = self._manage_selected_tree_item()
        current = ""
        if selected is not None:
            selected_path = str(
                selected.data(0, self._manage_tree_path_role()) or ""
            ).strip()
            selected_type = str(
                selected.data(0, self._manage_tree_type_role()) or "file"
            )
            if selected_type == "dir":
                current = selected_path
            else:
                current = self._manage_parent_directory(selected_path)
        if not current:
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

        current_dir = str(listing.get("directory") or remote_dir).strip()
        entries = list(listing.get("entries") or [])
        self.manage_file_tree.clear()
        root_item = self._manage_create_tree_item(
            name=self._manage_tree_root_display_name(current_dir),
            remote_path=current_dir,
            entry_type="dir",
            loaded=True,
        )
        shown_count = self._manage_populate_tree_children(root_item, entries)
        self.manage_file_tree.addTopLevelItem(root_item)
        root_item.setExpanded(True)
        self.manage_file_tree.setCurrentItem(root_item)

        self.manage_current_directory = current_dir
        self.manage_current_dir_label.setText(f"Tree root: {current_dir}")
        self.manage_remote_dir_edit.setText(current_dir)
        self.manage_current_remote_file = None
        self.manage_current_file_label.setText("No file loaded.")
        self.manage_file_editor.clear()
        self._append_manage_log(
            f"Loaded {shown_count} entries from {current_dir}."
        )
        self._set_device_connection_health(True, f"Host {params['host']} reachable.")
        self.statusBar().showMessage(f"Loaded {shown_count} entries", 2500)

    def _manage_file_selection_changed(self) -> None:
        item = self._manage_selected_tree_item()
        if item is None:
            return
        remote_path = str(item.data(0, self._manage_tree_path_role()) or "").strip()
        entry_type = str(item.data(0, self._manage_tree_type_role()) or "file")
        if not remote_path:
            return
        if entry_type == "dir":
            self.manage_current_directory = remote_path
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

        selected = self._manage_selected_tree_item()
        if selected is None:
            self._show_error("Manage Printer", "Select a remote file or folder first.")
            return
        remote_path = str(selected.data(0, self._manage_tree_path_role()) or "").strip()
        entry_type = str(selected.data(0, self._manage_tree_type_role()) or "file")
        if not remote_path:
            self._show_error("Manage Printer", "Selected item has an invalid file path.")
            return
        if entry_type == "dir":
            if self._manage_load_tree_item(selected, service=service, params=params):
                selected.setExpanded(True)
                self.manage_current_directory = remote_path
                self.manage_remote_dir_edit.setText(remote_path)
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
        self._set_persistent_preview_source(
            content=content,
            label=remote_path,
            source_kind="manage_remote",
            source_key=f"manage_remote:{remote_path}",
            update_last=True,
        )
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
            selected = self._manage_selected_tree_item()
            if selected is not None:
                selected_type = str(selected.data(0, self._manage_tree_type_role()) or "file")
                if selected_type != "file":
                    self._show_error("Manage Printer", "Select and open a file before saving.")
                    return
                remote_path = str(
                    selected.data(0, self._manage_tree_path_role()) or ""
                ).strip()
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
        self._set_persistent_preview_source(
            content=content,
            label=saved_path,
            source_kind="manage_remote",
            source_key=f"manage_remote:{saved_path}",
            update_last=True,
        )
        self._append_manage_log(f"Saved {saved_path}.")
        self._set_device_connection_health(True, f"Saved {saved_path}.")
        self.statusBar().showMessage("Remote file saved", 2500)

    def _manage_current_cfg_context(self) -> tuple[str, str] | None:
        remote_path = (self.manage_current_remote_file or "").strip()
        if not remote_path:
            self._show_error("Manage Printer", "Open a remote .cfg file first.")
            return None
        if not remote_path.lower().endswith(".cfg"):
            self._show_error("Manage Printer", "Current remote file is not a .cfg file.")
            return None
        return self.manage_file_editor.toPlainText(), remote_path

    def _manage_validate_current_file(self) -> None:
        context = self._manage_current_cfg_context()
        if context is None:
            return
        content, remote_path = context
        report = self.firmware_tools_service.validate_cfg(content, source_label=remote_path)
        blocking = sum(1 for finding in report.findings if finding.severity == "blocking")
        warnings = sum(1 for finding in report.findings if finding.severity == "warning")
        self._set_preview_validation_state(
            f"manage_remote:{remote_path}",
            blocking=blocking,
            warnings=warnings,
        )
        self._append_manage_log(
            f"Validation for {remote_path}: blocking={blocking}, warnings={warnings}."
        )
        self._set_device_connection_health(
            blocking == 0,
            f"{remote_path}: blocking={blocking}, warnings={warnings}",
        )
        if blocking > 0:
            details = self._build_cfg_validation_details(report)
            self._show_error(
                "Manage Printer",
                f"{remote_path}: blocking={blocking}, warnings={warnings}\n\n{details}",
            )
        elif warnings > 0:
            QMessageBox.warning(
                self,
                "Manage Printer",
                f"{remote_path}: warnings={warnings}\n\n{self._build_cfg_validation_details(report)}",
            )
        else:
            self.statusBar().showMessage("Remote firmware validation passed", 3000)

    def _manage_refactor_current_file(self) -> None:
        context = self._manage_current_cfg_context()
        if context is None:
            return
        content, remote_path = context
        updated, changes = self.firmware_tools_service.refactor_cfg(content)
        if updated != content:
            self.manage_file_editor.setPlainText(updated)
            self._set_persistent_preview_source(
                content=updated,
                label=remote_path,
                source_kind="manage_remote",
                source_key=f"manage_remote:{remote_path}",
                update_last=True,
            )
            self._append_manage_log(f"Refactored {remote_path}: {changes} change(s).")
            self.statusBar().showMessage(f"Refactored remote file ({changes} change(s))", 3000)
        else:
            self._append_manage_log(f"No refactor changes for {remote_path}.")
            self.statusBar().showMessage("No refactor changes for remote file", 2500)
        self._manage_validate_current_file()

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

    def _get_ssh_service(self, *, show_errors: bool = True) -> SSHDeployService | None:
        if self.ssh_service is not None:
            return self.ssh_service
        try:
            self.ssh_service = SSHDeployService()
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            if show_errors:
                self._show_error("SSH Unavailable", str(exc))
            else:
                self._append_ssh_log(f"SSH unavailable: {exc}")
            return None
        return self.ssh_service

    def _collect_ssh_params(
        self,
        host_override: str | None = None,
        *,
        show_errors: bool = True,
    ) -> dict[str, Any] | None:
        host = host_override.strip() if host_override else self.ssh_host_edit.text().strip()
        username = self.ssh_username_edit.text().strip()
        if not host or not username:
            if show_errors:
                self._show_error("SSH Input Error", "Host and username are required.")
            return None

        key_path = self.ssh_key_path_edit.text().strip() or None
        password = self.ssh_password_edit.text() or None
        if key_path and not Path(key_path).exists():
            if show_errors:
                self._show_error("SSH Input Error", f"SSH key does not exist: {key_path}")
            return None

        return {
            "host": host,
            "port": self.ssh_port_spin.value(),
            "username": username,
            "password": password,
            "key_path": key_path,
        }

    def _refresh_saved_connection_profiles(self, select_name: str | None = None) -> None:
        try:
            names = self.saved_connection_service.list_names()
        except OSError as exc:
            self._append_ssh_log(f"Failed to load saved connections: {exc}")
            self._update_default_connection_ui([])
            self._refresh_tools_connect_menu()
            return

        if hasattr(self, "ssh_saved_connection_combo"):
            self.ssh_saved_connection_combo.blockSignals(True)
            self.ssh_saved_connection_combo.clear()
            self.ssh_saved_connection_combo.addItems(names)
            self.ssh_saved_connection_combo.blockSignals(False)

            target_name = (select_name or "").strip()
            if not target_name:
                default_name = self.default_ssh_connection_name.strip()
                if default_name and default_name in names:
                    target_name = default_name

            if target_name:
                index = self.ssh_saved_connection_combo.findText(target_name)
                if index >= 0:
                    self.ssh_saved_connection_combo.setCurrentIndex(index)
                elif self.ssh_saved_connection_combo.count() > 0:
                    self.ssh_saved_connection_combo.setCurrentIndex(0)
            elif self.ssh_saved_connection_combo.count() > 0:
                self.ssh_saved_connection_combo.setCurrentIndex(0)

        self._update_default_connection_ui(names)
        self._refresh_tools_connect_menu()

    def _refresh_tools_connect_menu(self) -> None:
        if not hasattr(self, "tools_connect_menu"):
            return

        default_name = self.default_ssh_connection_name.strip()
        self.tools_connect_menu.clear()
        self.tools_connect_action = QAction("Current SSH Fields", self.tools_connect_menu)
        self.tools_connect_action.triggered.connect(self._connect_ssh_to_host)
        self.tools_connect_menu.addAction(self.tools_connect_action)
        self.tools_connect_menu.addSeparator()

        try:
            saved_profiles = self.saved_connection_service.list_names()
        except OSError as exc:
            error_action = QAction(
                f"(Failed to load saved connections: {exc})",
                self.tools_connect_menu,
            )
            error_action.setEnabled(False)
            self.tools_connect_menu.addAction(error_action)
            return

        if not saved_profiles:
            empty_action = QAction("(No saved connections)", self.tools_connect_menu)
            empty_action.setEnabled(False)
            self.tools_connect_menu.addAction(empty_action)
            return

        for profile_name in saved_profiles:
            label = profile_name
            if default_name and profile_name == default_name:
                label = f"{profile_name} (default)"
            profile_action = QAction(label, self.tools_connect_menu)
            profile_action.triggered.connect(
                lambda checked=False, name=profile_name: self._connect_saved_connection(name)
            )
            self.tools_connect_menu.addAction(profile_action)

    def _build_connection_profile_payload(self) -> dict[str, Any] | None:
        host = self.ssh_host_edit.text().strip()
        username = self.ssh_username_edit.text().strip()
        if not host or not username:
            self._show_error("SSH Input Error", "Host and username are required to save a profile.")
            return None
        return {
            "host": host,
            "port": int(self.ssh_port_spin.value()),
            "username": username,
            "password": self.ssh_password_edit.text() or None,
            "key_path": self.ssh_key_path_edit.text().strip() or None,
            "remote_dir": self.ssh_remote_dir_edit.text().strip() or "~/printer_data/config",
            "remote_file": self.ssh_remote_fetch_path_edit.text().strip()
            or "~/printer_data/config/printer.cfg",
        }

    def _save_named_connection_profile(self, name: str, announce: bool = True) -> bool:
        profile_name = name.strip()
        if not profile_name:
            self._show_error("Saved Connections", "Connection name is required.")
            return False
        payload = self._build_connection_profile_payload()
        if payload is None:
            return False
        try:
            self.saved_connection_service.save(profile_name, payload)
        except (OSError, ValueError) as exc:
            self._show_error("Saved Connections", str(exc))
            return False

        if not self.default_ssh_connection_name:
            try:
                saved_names = self.saved_connection_service.list_names()
            except OSError:
                saved_names = []
            if len(saved_names) == 1 and saved_names[0] == profile_name:
                self._persist_default_ssh_connection(profile_name)

        self._refresh_saved_connection_profiles(select_name=profile_name)
        self.ssh_connection_name_edit.setText(profile_name)
        if announce:
            self._append_ssh_log(f"Saved connection profile '{profile_name}'.")
            self.statusBar().showMessage(f"Saved connection '{profile_name}'", 2500)
        return True

    def _save_current_connection_profile(self) -> None:
        profile_name = self.ssh_connection_name_edit.text().strip()
        if not profile_name:
            self._show_error(
                "Saved Connections",
                "Set a connection name before saving.",
            )
            return
        self._save_named_connection_profile(profile_name)

    def _load_selected_saved_connection(self) -> None:
        profile_name = self.ssh_saved_connection_combo.currentText().strip()
        if not profile_name:
            self._show_error("Saved Connections", "No saved connection selected.")
            return
        self._load_saved_connection_profile(profile_name)

    def _load_saved_connection_profile(self, profile_name: str) -> bool:
        profile_name = profile_name.strip()
        if not profile_name:
            self._show_error("Saved Connections", "No saved connection selected.")
            return False
        profile = self.saved_connection_service.load(profile_name)
        if profile is None:
            self._show_error("Saved Connections", f"Connection '{profile_name}' was not found.")
            self._refresh_saved_connection_profiles()
            return False

        self.ssh_connection_name_edit.setText(profile_name)
        if hasattr(self, "ssh_saved_connection_combo"):
            index = self.ssh_saved_connection_combo.findText(profile_name)
            if index >= 0:
                self.ssh_saved_connection_combo.setCurrentIndex(index)
        self.ssh_host_edit.setText(str(profile.get("host") or ""))
        try:
            port_value = int(profile.get("port") or 22)
        except (TypeError, ValueError):
            port_value = 22
        self.ssh_port_spin.setValue(port_value)
        self.ssh_username_edit.setText(str(profile.get("username") or ""))
        self.ssh_password_edit.setText(str(profile.get("password") or ""))
        self.ssh_key_path_edit.setText(str(profile.get("key_path") or ""))
        self.ssh_remote_dir_edit.setText(
            str(profile.get("remote_dir") or "~/printer_data/config")
        )
        self.ssh_remote_fetch_path_edit.setText(
            str(profile.get("remote_file") or "~/printer_data/config/printer.cfg")
        )
        self.manage_host_edit.setText(self.ssh_host_edit.text().strip())
        self.manage_remote_dir_edit.setText(self.ssh_remote_dir_edit.text().strip())
        self.modify_remote_cfg_path_edit.setText(self.ssh_remote_fetch_path_edit.text().strip())
        self.modify_current_remote_file = None
        self._refresh_modify_connection_summary()
        self._append_ssh_log(f"Loaded connection profile '{profile_name}'.")
        self._append_modify_log(f"Loaded connection profile '{profile_name}'.")
        self.statusBar().showMessage(f"Loaded connection '{profile_name}'", 2500)
        return True

    def _connect_saved_connection(self, profile_name: str) -> None:
        if not self._load_saved_connection_profile(profile_name):
            return
        self._connect_ssh_to_host()

    def _delete_selected_saved_connection(self) -> None:
        profile_name = self.ssh_saved_connection_combo.currentText().strip()
        if not profile_name:
            self._show_error("Saved Connections", "No saved connection selected.")
            return
        answer = QMessageBox.question(
            self,
            "Delete Saved Connection",
            f"Delete saved connection '{profile_name}'?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        deleted = self.saved_connection_service.delete(profile_name)
        if deleted:
            if self.default_ssh_connection_name == profile_name:
                self._persist_default_ssh_connection("")
            self._append_ssh_log(f"Deleted connection profile '{profile_name}'.")
            self.statusBar().showMessage(f"Deleted connection '{profile_name}'", 2500)
            self._refresh_saved_connection_profiles()
            if self.ssh_connection_name_edit.text().strip() == profile_name:
                self.ssh_connection_name_edit.clear()
            return
        self._show_error("Saved Connections", f"Connection '{profile_name}' was not found.")

    def _save_successful_connection_profile(self) -> None:
        if not self.ssh_save_on_success_checkbox.isChecked():
            return
        profile_name = self.ssh_connection_name_edit.text().strip()
        if not profile_name:
            self._append_ssh_log(
                "Connected. Set 'Connection name' to save this profile for reconnection."
            )
            return
        self._save_named_connection_profile(profile_name, announce=True)

    def _append_ssh_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        self.ssh_log.appendPlainText(line)
        if hasattr(self, "console_activity_log"):
            self.console_activity_log.appendPlainText(f"[SSH] {line}")

    def _append_modify_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        self.modify_log.appendPlainText(line)
        if hasattr(self, "console_activity_log"):
            self.console_activity_log.appendPlainText(f"[MODIFY] {line}")

    def _set_modify_status(self, message: str, severity: str = "info") -> None:
        style_by_severity = {
            "ok": (
                "QLabel {"
                " background-color: #14532d;"
                " color: #ffffff;"
                " border: 1px solid #16a34a;"
                " border-radius: 4px;"
                " padding: 6px 8px;"
                " font-weight: 600;"
                "}"
            ),
            "warning": (
                "QLabel {"
                " background-color: #78350f;"
                " color: #ffffff;"
                " border: 1px solid #f59e0b;"
                " border-radius: 4px;"
                " padding: 6px 8px;"
                " font-weight: 600;"
                "}"
            ),
            "error": (
                "QLabel {"
                " background-color: #7f1d1d;"
                " color: #ffffff;"
                " border: 1px solid #ef4444;"
                " border-radius: 4px;"
                " padding: 6px 8px;"
                " font-weight: 600;"
                "}"
            ),
            "info": (
                "QLabel {"
                " background-color: #111827;"
                " color: #e5e7eb;"
                " border: 1px solid #374151;"
                " border-radius: 4px;"
                " padding: 6px 8px;"
                "}"
            ),
        }
        self.modify_status_label.setText(message)
        self.modify_status_label.setStyleSheet(style_by_severity.get(severity, style_by_severity["info"]))

    def _modify_connect(self) -> None:
        self._connect_ssh_to_host()
        if self.device_connected:
            self._set_modify_status("SSH connection ready for modify workflow.", severity="ok")
            self._append_modify_log("SSH connection verified for modify workflow.")

    def _modify_open_remote_cfg(self) -> None:
        service = self._get_ssh_service()
        if service is None:
            return

        params = self._collect_ssh_params()
        if params is None:
            return

        remote_path = self.modify_remote_cfg_path_edit.text().strip()
        if not remote_path:
            self._show_error("Modify Existing", "Remote .cfg path is required.")
            return

        self._append_modify_log(f"Opening remote file: {remote_path}")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            contents = service.fetch_file(remote_path=remote_path, **params)
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._set_modify_status(str(exc), severity="error")
            self._append_modify_log(f"Open failed: {exc}")
            self._show_error("Modify Existing", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.modify_editor.setPlainText(contents)
        self.modify_current_remote_file = remote_path
        self._set_persistent_preview_source(
            content=contents,
            label=remote_path,
            source_kind="modify_remote",
            source_key=f"modify_remote:{remote_path}",
            update_last=True,
        )
        self._set_device_connection_health(True, f"Opened {remote_path}.")
        self._set_modify_status(f"Loaded {remote_path}", severity="ok")
        self._append_modify_log(f"Loaded {remote_path}.")
        self.statusBar().showMessage(f"Loaded {remote_path}", 2500)

    def _modify_current_cfg_context(self) -> tuple[str, str] | None:
        remote_path = self.modify_remote_cfg_path_edit.text().strip()
        if not remote_path:
            remote_path = (self.modify_current_remote_file or "").strip()
        if not remote_path:
            self._show_error("Modify Existing", "Open a remote .cfg file first.")
            return None
        if not remote_path.lower().endswith(".cfg"):
            self._show_error("Modify Existing", "Current remote file is not a .cfg file.")
            return None
        content = self.modify_editor.toPlainText()
        return content, remote_path

    def _modify_validate_current_file(self) -> None:
        context = self._modify_current_cfg_context()
        if context is None:
            return

        content, remote_path = context
        report = self.firmware_tools_service.validate_cfg(content, source_label=remote_path)
        blocking = sum(1 for finding in report.findings if finding.severity == "blocking")
        warnings = sum(1 for finding in report.findings if finding.severity == "warning")
        self._set_preview_validation_state(
            f"modify_remote:{remote_path}",
            blocking=blocking,
            warnings=warnings,
        )
        self._append_modify_log(
            f"Validation for {remote_path}: blocking={blocking}, warnings={warnings}."
        )

        if blocking > 0:
            self._set_modify_status(
                f"{remote_path}: {blocking} blocking and {warnings} warning issue(s).",
                severity="error",
            )
            self._set_device_connection_health(
                False,
                f"{remote_path}: blocking={blocking}, warnings={warnings}",
            )
            details = self._build_cfg_validation_details(report)
            self._show_error(
                "Modify Existing",
                f"{remote_path}: blocking={blocking}, warnings={warnings}\n\n{details}",
            )
            return

        if warnings > 0:
            self._set_modify_status(
                f"{remote_path}: warnings detected ({warnings}).",
                severity="warning",
            )
            self._set_device_connection_health(
                True,
                f"{remote_path}: blocking=0, warnings={warnings}",
            )
            QMessageBox.warning(
                self,
                "Modify Existing",
                f"{remote_path}: warnings={warnings}\n\n{self._build_cfg_validation_details(report)}",
            )
            return

        self._set_modify_status(f"{remote_path}: validation passed.", severity="ok")
        self._set_device_connection_health(True, f"{remote_path}: validation passed.")
        self.statusBar().showMessage("Modify workflow validation passed", 2500)

    def _modify_refactor_current_file(self) -> None:
        context = self._modify_current_cfg_context()
        if context is None:
            return
        content, remote_path = context
        updated, changes = self.firmware_tools_service.refactor_cfg(content)
        if updated != content:
            self.modify_editor.setPlainText(updated)
            self._set_persistent_preview_source(
                content=updated,
                label=remote_path,
                source_kind="modify_remote",
                source_key=f"modify_remote:{remote_path}",
                update_last=True,
            )
            self._append_modify_log(f"Refactored {remote_path}: {changes} change(s).")
            self._set_modify_status(
                f"Refactored {remote_path}: {changes} change(s).",
                severity="info",
            )
            self.statusBar().showMessage(f"Refactored {remote_path}", 2500)
        else:
            self._append_modify_log(f"No refactor changes for {remote_path}.")
            self._set_modify_status(f"No refactor changes for {remote_path}.", severity="info")
        self._modify_validate_current_file()

    def _modify_upload_current_file(self) -> None:
        service = self._get_ssh_service()
        if service is None:
            return
        params = self._collect_ssh_params()
        if params is None:
            return

        context = self._modify_current_cfg_context()
        if context is None:
            return
        content, remote_path = context
        if not content.strip():
            self._show_error("Modify Existing", "Current editor content is empty.")
            return

        backup_root = self.modify_backup_root_edit.text().strip() or "~/klippconfig_backups"
        remote_dir = posixpath.dirname(remote_path.rstrip("/")) or "."

        self.app_state_store.update_deploy(upload_in_progress=True)
        self.action_log_service.log_event(
            "upload",
            phase="start",
            mode="modify_existing",
            host=str(params["host"]),
            remote_dir=remote_dir,
            remote_path=remote_path,
        )
        self._append_modify_log(f"Creating backup from {remote_dir} into {backup_root}.")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            backup_path = service.create_backup(
                remote_dir=remote_dir,
                backup_root=backup_root,
                **params,
            )
            saved_path = service.write_file(
                remote_path=remote_path,
                content=content,
                **params,
            )
        except SSHDeployError as exc:
            self.app_state_store.update_deploy(
                upload_in_progress=False,
                last_upload_status=f"failed: {exc}",
            )
            self._set_device_connection_health(False, str(exc))
            self._set_modify_status(str(exc), severity="error")
            self._append_modify_log(f"Upload failed: {exc}")
            self._show_error("Modify Existing", str(exc))
            self.action_log_service.log_event(
                "upload",
                phase="failed",
                mode="modify_existing",
                host=str(params["host"]),
                remote_path=remote_path,
                error=str(exc),
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.modify_current_remote_file = saved_path
        self.modify_remote_cfg_path_edit.setText(saved_path)
        self._set_persistent_preview_source(
            content=content,
            label=saved_path,
            source_kind="modify_remote",
            source_key=f"modify_remote:{saved_path}",
            update_last=True,
        )
        self._append_modify_log(f"Backup created: {backup_path}")
        self._append_modify_log(f"Uploaded file: {saved_path}")
        self._set_modify_status(
            f"Uploaded {saved_path} (backup: {backup_path})",
            severity="ok",
        )
        self.app_state_store.update_deploy(
            upload_in_progress=False,
            last_upload_status=f"uploaded:{saved_path}",
        )
        self.action_log_service.log_event(
            "upload",
            phase="complete",
            mode="modify_existing",
            host=str(params["host"]),
            remote_path=saved_path,
            backup_path=str(backup_path),
        )
        self._set_device_connection_health(True, f"Uploaded {saved_path}.")
        self.statusBar().showMessage("Modify workflow upload complete", 3000)

    def _modify_test_restart(self) -> None:
        service = self._get_ssh_service()
        if service is None:
            return
        params = self._collect_ssh_params()
        if params is None:
            return

        restart_command = self.ssh_restart_cmd_edit.text().strip() or "sudo systemctl restart klipper"
        self.action_log_service.log_event(
            "restart",
            phase="start",
            action_name="Modify Existing Restart",
            command=restart_command,
            host=str(params["host"]),
        )
        self._append_modify_log(f"Running restart/status command: {restart_command}")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            output = service.run_remote_command(
                command=restart_command,
                **params,
            ).strip()
        except SSHDeployError as exc:
            self.app_state_store.update_deploy(last_restart_status=f"failed: {exc}")
            self._set_device_connection_health(False, str(exc))
            self._set_modify_status(str(exc), severity="error")
            self._append_modify_log(f"Restart test failed: {exc}")
            self._show_error("Modify Existing", str(exc))
            self.action_log_service.log_event(
                "restart",
                phase="failed",
                action_name="Modify Existing Restart",
                command=restart_command,
                host=str(params["host"]),
                error=str(exc),
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        summary = output or "(no output)"
        self.app_state_store.update_deploy(last_restart_status=summary)
        self.action_log_service.log_event(
            "restart",
            phase="complete",
            action_name="Modify Existing Restart",
            command=restart_command,
            host=str(params["host"]),
            output=summary,
        )
        self._set_modify_status(f"Restart command succeeded: {summary}", severity="ok")
        self._append_modify_log(f"Restart output: {summary}")
        self._set_device_connection_health(True, f"Restart command succeeded on {params['host']}.")
        self.statusBar().showMessage("Restart test succeeded", 3000)

    def _resolve_connected_printer_name(self, host: str) -> str:
        name = self.ssh_connection_name_edit.text().strip()
        if name:
            return name
        saved_name = self.ssh_saved_connection_combo.currentText().strip()
        if saved_name:
            return saved_name
        return host

    def _apply_connect_success(
        self,
        params: dict[str, Any],
        output: str,
        *,
        source: str = "manual",
    ) -> None:
        self._set_device_connection_health(True, str(output))
        printer_name = self._resolve_connected_printer_name(str(params["host"]))
        self.preview_connected_printer_name = printer_name
        self.preview_connected_host = str(params["host"])
        self._set_manage_connected_printer_display(
            printer_name=printer_name,
            host=str(params["host"]),
            connected=True,
        )
        self._set_modify_connected_printer_display(
            printer_name=printer_name,
            host=str(params["host"]),
            connected=True,
        )
        self.manage_host_edit.setText(str(params["host"]).strip())
        self._append_manage_log(f"Connected printer: {printer_name} ({params['host']})")
        self._append_ssh_log(f"Connected: {output}")
        self._append_modify_log(f"Connected: {output}")
        self._set_modify_status(f"Connected to {printer_name}", severity="ok")
        self._save_successful_connection_profile()
        if source == "startup":
            self.statusBar().showMessage(f"Auto-connected to {printer_name}", 3000)
        else:
            self.statusBar().showMessage(f"Connected to {printer_name}", 2500)
        self.action_log_service.log_event(
            "connect",
            phase="complete",
            host=str(params["host"]),
            printer_name=printer_name,
            output=str(output),
            source=source,
        )

    def _apply_connect_failure(
        self,
        params: dict[str, Any],
        output: str,
        *,
        source: str = "manual",
        show_error_dialog: bool = False,
        use_failure_prefix: bool = True,
    ) -> None:
        self._set_device_connection_health(False, str(output))
        self.preview_connected_printer_name = None
        self.preview_connected_host = None
        self._set_manage_connected_printer_display(None, None, connected=False)
        self._set_modify_connected_printer_display(None, None, connected=False)
        if use_failure_prefix:
            self._append_ssh_log(f"Connection failed: {output}")
            self._append_modify_log(f"Connection failed: {output}")
        else:
            self._append_ssh_log(str(output))
            self._append_modify_log(f"Connect failed: {output}")
        self._set_modify_status(f"Connection failed: {output}", severity="error")
        if show_error_dialog:
            self._show_error("SSH Connect Failed", str(output))
        self.action_log_service.log_event(
            "connect",
            phase="failed",
            host=str(params.get("host") or ""),
            output=str(output),
            source=source,
        )

    def _connect_ssh_to_host(self) -> None:
        if self.auto_connect_in_progress:
            self.statusBar().showMessage("Connect already in progress...", 2500)
            return

        service = self._get_ssh_service()
        if service is None:
            return

        params = self._collect_ssh_params()
        if params is None:
            return

        self.action_log_service.log_event(
            "connect",
            phase="start",
            host=str(params["host"]),
            username=str(params["username"]),
            port=int(params["port"]),
        )
        self._append_ssh_log(
            f"Connecting to {params['username']}@{params['host']}:{params['port']}"
        )
        try:
            ok, output = service.test_connection(**params)
        except SSHDeployError as exc:
            self._apply_connect_failure(
                params,
                str(exc),
                source="manual",
                show_error_dialog=True,
                use_failure_prefix=False,
            )
            return

        if ok:
            self._apply_connect_success(params, str(output), source="manual")
            return
        self._apply_connect_failure(params, str(output), source="manual", show_error_dialog=False)

    def _test_ssh_connection(self) -> None:
        self._connect_ssh_to_host()

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
        self.app_state_store.update_ui(active_route="deploy", right_panel_mode="logs")
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
        self.app_state_store.update_deploy(upload_in_progress=True)
        self.action_log_service.log_event(
            "upload",
            phase="start",
            host=str(params["host"]),
            remote_dir=remote_dir,
            file_count=len(self.current_pack.files),
        )
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
            self.app_state_store.update_deploy(
                upload_in_progress=False,
                last_upload_status=f"failed: {exc}",
            )
            self._set_device_connection_health(False, str(exc))
            self._append_ssh_log(str(exc))
            self._show_error("Deploy Failed", str(exc))
            self.action_log_service.log_event(
                "upload",
                phase="failed",
                host=str(params["host"]),
                remote_dir=remote_dir,
                error=str(exc),
            )
            return

        uploaded = result.get("uploaded", [])
        backup_path = result.get("backup_path")
        restart_output = result.get("restart_output")

        if backup_path:
            self._append_ssh_log(f"Backup created: {backup_path}")
        self._append_ssh_log(f"Uploaded {len(uploaded)} files.")
        if restart_output:
            self._append_ssh_log(f"Restart output: {restart_output}")
        self.app_state_store.update_deploy(
            upload_in_progress=False,
            last_upload_status=f"uploaded:{len(uploaded)}",
            last_restart_status=str(restart_output or ""),
        )
        self.action_log_service.log_event(
            "upload",
            phase="complete",
            host=str(params["host"]),
            remote_dir=remote_dir,
            uploaded_count=len(uploaded),
            backup_path=str(backup_path or ""),
            restart_output=str(restart_output or ""),
        )
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

    def closeEvent(self, event) -> None:  # noqa: ANN001
        if hasattr(self, "auto_connect_poll_timer"):
            self.auto_connect_poll_timer.stop()
        self._persist_preview_settings()
        self.app_state_store.unsubscribe(self._on_app_state_changed)
        super().closeEvent(event)

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)



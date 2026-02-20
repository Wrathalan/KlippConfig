
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import posixpath
import re
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QPixmap
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
    QTreeWidget,
    QTreeWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.domain.models import Preset, ProjectConfig, RenderedPack, ValidationReport
from app.services.board_registry import (
    addon_supported_for_preset,
    get_addon_profile,
    get_board_profile,
    get_toolhead_board_profile,
    list_addons,
    list_main_boards,
    list_toolhead_boards,
)
from app.services.exporter import ExportService
from app.services.firmware_tools import FirmwareToolsService
from app.services.paths import creator_icon_path
from app.services.printer_discovery import (
    DiscoveredPrinter,
    PrinterDiscoveryError,
    PrinterDiscoveryService,
)
from app.services.preset_catalog import PresetCatalogError, PresetCatalogService
from app.services.project_store import ProjectStoreService
from app.services.renderer import ConfigRenderService
from app.services.saved_connections import SavedConnectionService
from app.services.ssh_deploy import SSHDeployError, SSHDeployService
from app.services.ui_scaling import UIScaleMode, UIScalingService
from app.services.validator import ValidationService
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


class MainWindow(QMainWindow):
    MACRO_PACK_OPTIONS = {
        "core_maintenance": "Core Maintenance",
        "qgl_helpers": "QGL Helpers",
        "filament_ops": "Filament Ops",
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
        saved_connection_service: SavedConnectionService | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"KlippConfig v{__version__}")
        self.resize(1380, 900)

        self.catalog_service = PresetCatalogService()
        self.render_service = ConfigRenderService()
        self.validation_service = ValidationService()
        self.firmware_tools_service = FirmwareToolsService()
        self.export_service = ExportService()
        self.project_store = ProjectStoreService()
        self.saved_connection_service = saved_connection_service or SavedConnectionService()
        self.ssh_service: SSHDeployService | None = None
        self.discovery_service = PrinterDiscoveryService()
        self.ui_scaling_service = ui_scaling_service or UIScalingService()
        self.active_scale_mode: UIScaleMode = self.ui_scaling_service.resolve_mode(
            saved=active_scale_mode or self.ui_scaling_service.load_mode()
        )
        self.ui_scale_actions: dict[UIScaleMode, QAction] = {}
        self.ui_scale_action_group: QActionGroup | None = None
        self.addon_options = self._build_addon_options()

        self.presets_by_id: dict[str, Preset] = {}
        self.current_preset: Preset | None = None
        self.current_project: ProjectConfig | None = None
        self.current_pack: RenderedPack | None = None
        self.current_report = ValidationReport()
        self.current_cfg_report = ValidationReport()

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
        self.device_connected = False
        self.manage_control_windows: list[QMainWindow] = []

        self._build_ui()
        self._load_presets()
        self._render_and_validate()

    def _build_addon_options(self) -> dict[str, str]:
        options: list[tuple[str, str]] = []
        for addon_id in list_addons():
            profile = get_addon_profile(addon_id)
            label = profile.label if profile is not None else addon_id
            options.append((addon_id, label))
        options.sort(key=lambda item: item[1].lower())
        return dict(options)

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

        self.main_tab = self._build_main_tab()
        self.wizard_tab = self._build_wizard_tab()
        self.files_tab = self._build_files_tab()
        self.live_deploy_tab = self._build_live_deploy_tab()
        self.modify_existing_tab = self._build_modify_existing_tab()
        self.manage_printer_tab = self._build_manage_printer_tab()
        self.about_tab = self._build_about_tab()

        self.tabs.addTab(self.main_tab, "Main")
        self.tabs.addTab(self.wizard_tab, "Configuration")
        self.tabs.addTab(self.files_tab, "Files")
        self.tabs.addTab(self.live_deploy_tab, "SSH")
        self.tabs.addTab(self.modify_existing_tab, "Modify Existing")
        self.tabs.addTab(self.manage_printer_tab, "Manage Printer")
        self.tabs.addTab(self.about_tab, "About")

        self.setCentralWidget(root)
        self._build_footer_connection_health()
        self._set_manage_connected_printer_display(None, None, connected=False)
        self._set_modify_connected_printer_display(None, None, connected=False)
        self._refresh_modify_connection_summary()
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

    def _go_to_configuration_tab(self) -> None:
        self.tabs.setCurrentWidget(self.wizard_tab)

    def _go_to_modify_existing_tab(self) -> None:
        self.tabs.setCurrentWidget(self.modify_existing_tab)

    def _go_to_ssh_tab(self) -> None:
        self.tabs.setCurrentWidget(self.live_deploy_tab)

    def _go_to_about_tab(self) -> None:
        self.tabs.setCurrentWidget(self.about_tab)

    def _build_main_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        title = QLabel("KlippConfig Main", tab)
        title.setStyleSheet("QLabel { font-size: 22px; font-weight: 700; }")
        layout.addWidget(title)

        subtitle = QLabel(
            "Choose your workflow entry point. Existing SSH, Manage Printer, and Files tools remain available.",
            tab,
        )
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        actions_group = QGroupBox("Start", tab)
        actions_layout = QVBoxLayout(actions_group)
        actions_layout.setSpacing(10)

        self.main_new_firmware_btn = QPushButton("New Firmware", actions_group)
        self.main_new_firmware_btn.clicked.connect(self._go_to_configuration_tab)
        actions_layout.addWidget(self.main_new_firmware_btn)
        actions_layout.addWidget(
            QLabel(
                "Open Configuration to build a new printer profile from presets.",
                actions_group,
            )
        )

        self.main_modify_existing_btn = QPushButton("Modify Existing", actions_group)
        self.main_modify_existing_btn.clicked.connect(self._go_to_modify_existing_tab)
        actions_layout.addWidget(self.main_modify_existing_btn)
        actions_layout.addWidget(
            QLabel(
                "Open the remote workflow for live SSH config editing, upload, and restart testing.",
                actions_group,
            )
        )

        self.main_connect_manage_btn = QPushButton("Connect/Manage Printer", actions_group)
        self.main_connect_manage_btn.clicked.connect(self._go_to_ssh_tab)
        actions_layout.addWidget(self.main_connect_manage_btn)
        actions_layout.addWidget(
            QLabel(
                "Go to SSH for connect/discovery/deploy and then use Manage Printer for direct file operations.",
                actions_group,
            )
        )

        self.main_about_btn = QPushButton("About", actions_group)
        self.main_about_btn.clicked.connect(self._go_to_about_tab)
        actions_layout.addWidget(self.main_about_btn)
        actions_layout.addWidget(QLabel("View mission, creator info, and platform details.", actions_group))

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

        self.modify_validate_btn = QPushButton("Validate", tab)
        self.modify_validate_btn.clicked.connect(self._modify_validate_current_file)
        action_row.addWidget(self.modify_validate_btn)

        self.modify_upload_btn = QPushButton("Upload", tab)
        self.modify_upload_btn.clicked.connect(self._modify_upload_current_file)
        action_row.addWidget(self.modify_upload_btn)

        self.modify_test_restart_btn = QPushButton("Test Restart", tab)
        self.modify_test_restart_btn.clicked.connect(self._modify_test_restart)
        action_row.addWidget(self.modify_test_restart_btn)

        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.modify_status_label = QLabel("No remote file loaded.", tab)
        self.modify_status_label.setWordWrap(True)
        layout.addWidget(self.modify_status_label)

        self.modify_editor = QPlainTextEdit(tab)
        layout.addWidget(self.modify_editor, 1)

        (
            modify_log_section,
            self.modify_log_section_toggle,
            self.modify_log_section_content,
            modify_log_layout,
        ) = self._build_collapsible_section("Console Log", tab, expanded=False)

        self.modify_log = QPlainTextEdit(self.modify_log_section_content)
        self.modify_log.setReadOnly(True)
        self.modify_log.setMaximumBlockCount(2000)
        modify_log_layout.addWidget(self.modify_log, 1)
        layout.addWidget(modify_log_section, 1)

        self.ssh_host_edit.textChanged.connect(self._refresh_modify_connection_summary)
        self.ssh_username_edit.textChanged.connect(self._refresh_modify_connection_summary)
        self.ssh_port_spin.valueChanged.connect(self._refresh_modify_connection_summary)
        self.ssh_key_path_edit.textChanged.connect(self._refresh_modify_connection_summary)
        self.ssh_restart_cmd_edit.textChanged.connect(self._refresh_modify_connection_summary)
        self.ssh_remote_fetch_path_edit.textChanged.connect(self._sync_modify_remote_cfg_from_ssh)

        self._refresh_modify_connection_summary()
        return tab

    def _build_about_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)

        scroll = QScrollArea(tab)
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
            'Join the KlippConfig Discord: <a href="https://discord.gg/bbnAtfbY5C">https://discord.gg/bbnAtfbY5C</a>',
            community_group,
        )
        discord_label.setOpenExternalLinks(True)
        discord_label.setWordWrap(True)
        community_layout.addWidget(discord_label)
        content_layout.addWidget(community_group)

        content_layout.addStretch(1)
        scroll.setWidget(content)
        return tab

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
        for key, label in self.addon_options.items():
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

        self.refactor_cfg_btn = QPushButton("Refactor Current .cfg", tab)
        self.refactor_cfg_btn.clicked.connect(self._refactor_current_cfg_file)
        top_row.addWidget(self.refactor_cfg_btn)

        self.validate_cfg_btn = QPushButton("Validate Current .cfg", tab)
        self.validate_cfg_btn.clicked.connect(self._validate_current_cfg_file)
        top_row.addWidget(self.validate_cfg_btn)

        self.preview_path_label = QLabel("No file selected.", tab)
        top_row.addWidget(self.preview_path_label, 1)

        layout.addLayout(top_row)

        self.cfg_tools_status_label = QLabel("", tab)
        self.cfg_tools_status_label.setWordWrap(True)
        self.cfg_tools_status_label.setVisible(False)
        layout.addWidget(self.cfg_tools_status_label)

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

        self.ssh_save_on_success_checkbox = QCheckBox(
            "Save on successful connect (uses Connection name)",
            connection_group,
        )
        self.ssh_save_on_success_checkbox.setChecked(True)
        connection_form.addRow(self.ssh_save_on_success_checkbox)

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

        self.ssh_connect_btn = QPushButton("Connect", tab)
        self.ssh_connect_btn.clicked.connect(self._connect_ssh_to_host)
        button_row.addWidget(self.ssh_connect_btn)

        fetch_btn = QPushButton("Open Remote File", tab)
        fetch_btn.clicked.connect(self._fetch_remote_cfg_file)
        button_row.addWidget(fetch_btn)

        self.deploy_btn = QPushButton("Deploy Generated Pack", tab)
        self.deploy_btn.clicked.connect(self._deploy_generated_pack)
        button_row.addWidget(self.deploy_btn)

        button_row.addStretch(1)
        layout.addLayout(button_row)

        (
            ssh_log_section,
            self.ssh_log_section_toggle,
            self.ssh_log_section_content,
            ssh_log_layout,
        ) = self._build_collapsible_section("Console Log", tab, expanded=False)

        self.ssh_log = QPlainTextEdit(self.ssh_log_section_content)
        self.ssh_log.setReadOnly(True)
        self.ssh_log.setMaximumBlockCount(2000)
        ssh_log_layout.addWidget(self.ssh_log, 1)
        layout.addWidget(ssh_log_section, 1)
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

        self.manage_validate_file_btn = QPushButton("Validate Current .cfg", tab)
        self.manage_validate_file_btn.clicked.connect(self._manage_validate_current_file)
        action_row.addWidget(self.manage_validate_file_btn)

        self.manage_open_control_btn = QPushButton("Open Control Window", tab)
        self.manage_open_control_btn.clicked.connect(self._manage_open_control_window)
        action_row.addWidget(self.manage_open_control_btn)

        action_row.addStretch(1)
        layout.addLayout(action_row)

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

        (
            manage_log_section,
            self.manage_log_section_toggle,
            self.manage_log_section_content,
            manage_log_layout,
        ) = self._build_collapsible_section("Console Log", tab, expanded=False)

        self.manage_log = QPlainTextEdit(self.manage_log_section_content)
        self.manage_log.setReadOnly(True)
        self.manage_log.setMaximumBlockCount(1000)
        manage_log_layout.addWidget(self.manage_log, 1)
        layout.addWidget(manage_log_section, 1)

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

        available_boards = sorted(set(preset.supported_boards).union(list_main_boards()))
        available_toolheads = sorted(
            set(preset.supported_toolhead_boards).union(list_toolhead_boards())
        )
        self._populate_board_combo(available_boards)
        self._populate_toolhead_board_combo(available_toolheads)
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
        supported = {
            addon_name
            for addon_name in self.addon_checkboxes
            if addon_supported_for_preset(
                addon_name,
                preset_id=preset.id,
                preset_family=preset.family,
                preset_supported_addons=preset.supported_addons,
            )
        }
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
        if self._is_cfg_label(label, generated_name):
            self._run_current_cfg_validation(show_dialog=False)
        else:
            self._clear_cfg_tools_status()

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

    def _update_cfg_tools_status(self, report: ValidationReport, source_label: str) -> None:
        blocking = sum(1 for finding in report.findings if finding.severity == "blocking")
        warnings = sum(1 for finding in report.findings if finding.severity == "warning")
        total = blocking + warnings

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
        context = self._current_cfg_context(show_error=show_dialog)
        if context is None:
            return None
        content, source_label = context

        report = self.firmware_tools_service.validate_cfg(content, source_label=source_label)
        self.current_cfg_report = report
        self._update_cfg_tools_status(report, source_label=source_label)

        blocking = sum(1 for finding in report.findings if finding.severity == "blocking")
        warnings = sum(1 for finding in report.findings if finding.severity == "warning")
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
                    f"{source_label}: {blocking} blocking, {warnings} warning.\n\n{details}",
                )
            elif warnings > 0:
                QMessageBox.warning(
                    self,
                    "Firmware Validation",
                    f"{source_label}: warnings detected ({warnings}).\n\n{details}",
                )
            else:
                QMessageBox.information(
                    self,
                    "Firmware Validation",
                    f"{source_label}: no issues detected.",
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
            if (
                self.files_current_source == "generated"
                and self.current_pack is not None
                and self.files_current_generated_name
            ):
                self.current_pack.files[self.files_current_generated_name] = updated
            self.statusBar().showMessage(f"Refactored {source_label} ({changes} change(s))", 3000)
        else:
            self.statusBar().showMessage(f"No refactor changes for {source_label}", 2500)
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

    def _refresh_saved_connection_profiles(self, select_name: str | None = None) -> None:
        try:
            names = self.saved_connection_service.list_names()
        except OSError as exc:
            self._append_ssh_log(f"Failed to load saved connections: {exc}")
            return

        self.ssh_saved_connection_combo.blockSignals(True)
        self.ssh_saved_connection_combo.clear()
        self.ssh_saved_connection_combo.addItems(names)
        self.ssh_saved_connection_combo.blockSignals(False)

        target_name = (select_name or "").strip()
        if target_name:
            index = self.ssh_saved_connection_combo.findText(target_name)
            if index >= 0:
                self.ssh_saved_connection_combo.setCurrentIndex(index)
                return
        if self.ssh_saved_connection_combo.count() > 0:
            self.ssh_saved_connection_combo.setCurrentIndex(0)

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
        profile = self.saved_connection_service.load(profile_name)
        if profile is None:
            self._show_error("Saved Connections", f"Connection '{profile_name}' was not found.")
            self._refresh_saved_connection_profiles()
            return

        self.ssh_connection_name_edit.setText(profile_name)
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
        self.ssh_log.appendPlainText(f"[{stamp}] {message}")

    def _append_modify_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.modify_log.appendPlainText(f"[{stamp}] {message}")

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
            self._set_device_connection_health(False, str(exc))
            self._set_modify_status(str(exc), severity="error")
            self._append_modify_log(f"Upload failed: {exc}")
            self._show_error("Modify Existing", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        self.modify_current_remote_file = saved_path
        self.modify_remote_cfg_path_edit.setText(saved_path)
        self._append_modify_log(f"Backup created: {backup_path}")
        self._append_modify_log(f"Uploaded file: {saved_path}")
        self._set_modify_status(
            f"Uploaded {saved_path} (backup: {backup_path})",
            severity="ok",
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
        self._append_modify_log(f"Running restart/status command: {restart_command}")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            output = service.run_remote_command(
                command=restart_command,
                **params,
            ).strip()
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._set_modify_status(str(exc), severity="error")
            self._append_modify_log(f"Restart test failed: {exc}")
            self._show_error("Modify Existing", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        summary = output or "(no output)"
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

    def _connect_ssh_to_host(self) -> None:
        service = self._get_ssh_service()
        if service is None:
            return

        params = self._collect_ssh_params()
        if params is None:
            return

        self._append_ssh_log(
            f"Connecting to {params['username']}@{params['host']}:{params['port']}"
        )
        try:
            ok, output = service.test_connection(**params)
        except SSHDeployError as exc:
            self._set_device_connection_health(False, str(exc))
            self._set_manage_connected_printer_display(None, None, connected=False)
            self._set_modify_connected_printer_display(None, None, connected=False)
            self._append_ssh_log(str(exc))
            self._append_modify_log(f"Connect failed: {exc}")
            self._set_modify_status(str(exc), severity="error")
            self._show_error("SSH Connect Failed", str(exc))
            return

        if ok:
            self._set_device_connection_health(True, str(output))
            printer_name = self._resolve_connected_printer_name(str(params["host"]))
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
            self._append_manage_log(
                f"Connected printer: {printer_name} ({params['host']})"
            )
            self._append_ssh_log(f"Connected: {output}")
            self._append_modify_log(f"Connected: {output}")
            self._set_modify_status(f"Connected to {printer_name}", severity="ok")
            self._save_successful_connection_profile()
            self.statusBar().showMessage(f"Connected to {printer_name}", 2500)
            return
        self._set_device_connection_health(False, str(output))
        self._set_manage_connected_printer_display(None, None, connected=False)
        self._set_modify_connected_printer_display(None, None, connected=False)
        self._append_ssh_log(f"Connection failed: {output}")
        self._append_modify_log(f"Connection failed: {output}")
        self._set_modify_status(f"Connection failed: {output}", severity="error")

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


import json
from pathlib import Path
import time

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QGroupBox

from app.ui.main_window import MainWindow
from app.services.saved_connections import SavedConnectionService
from app.services.ssh_deploy import SSHDeployError


class FakeConnectionService:
    def __init__(self, ok: bool, output: str, delay_seconds: float = 0.0) -> None:
        self.ok = ok
        self.output = output
        self.delay_seconds = delay_seconds

    def test_connection(self, **_kwargs):
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        return self.ok, self.output


class FakeModifyWorkflowService:
    def __init__(self) -> None:
        self.open_content = (
            "[printer]\n"
            "kinematics=corexy\n"
            "max_velocity=250\n"
            "max_accel=3000\n"
            "square_corner_velocity=5\n\n"
            "[mcu]\n"
            "serial=/dev/serial/by-id/test\n\n"
            "[extruder]\n"
            "step_pin=PB1\n\n"
            "[heater_bed]\n"
            "heater_pin=PC8\n"
        )
        self.saved: tuple[str, str] | None = None
        self.backup_calls: list[tuple[str, str]] = []
        self.command_calls: list[str] = []
        self.fail_fetch = False
        self.fail_write = False
        self.fail_restart = False

    def test_connection(self, **_kwargs):
        return True, "connected"

    def fetch_file(self, **kwargs):
        if self.fail_fetch:
            raise SSHDeployError("fetch failed")
        return self.open_content

    def create_backup(self, **kwargs):
        remote_dir = kwargs.get("remote_dir") or ""
        backup_root = kwargs.get("backup_root") or ""
        self.backup_calls.append((str(remote_dir), str(backup_root)))
        return "/home/pi/klippconfig_backups/backup-20260101-010101"

    def write_file(self, **kwargs):
        if self.fail_write:
            raise SSHDeployError("write failed")
        self.saved = (kwargs["remote_path"], kwargs["content"])
        return kwargs["remote_path"]

    def run_remote_command(self, **kwargs):
        if self.fail_restart:
            raise SSHDeployError("restart failed")
        command = kwargs.get("command") or ""
        self.command_calls.append(str(command))
        return "restart ok"


def _select_first_mainboard(window: MainWindow) -> None:
    _select_default_voron_preset(window)
    if window.board_combo.count() > 1:
        window.board_combo.setCurrentIndex(1)


def _select_first_can_toolhead_board(window: MainWindow) -> None:
    _select_default_voron_preset(window)
    if window.toolhead_can_board_combo.count() > 1:
        window.toolhead_can_board_combo.setCurrentIndex(1)


def _select_default_voron_preset(window: MainWindow) -> None:
    preset_index = window.preset_combo.findData(MainWindow.DEFAULT_VORON_PRESET_ID)
    if preset_index < 0 and window.preset_combo.count() > 1:
        preset_index = 1
    if preset_index >= 0:
        window.preset_combo.setCurrentIndex(preset_index)


def _temp_settings(tmp_path: Path, name: str = "ui_settings.ini") -> QSettings:
    settings = QSettings(str(tmp_path / name), QSettings.Format.IniFormat)
    settings.clear()
    settings.sync()
    return settings


def test_tabs_hide_advanced_and_keep_files(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    labels = [window.tabs.tabText(index) for index in range(window.tabs.count())]
    assert labels == [
        "Main",
        "Configuration",
        "Files",
        "Printers",
        "Modify Existing",
        "Manage Printer",
    ]
    assert "Advanced" not in labels
    assert "Validation" not in labels
    assert "Export" not in labels
    assert "Files" in labels
    assert "About" not in labels
    assert hasattr(window, "export_folder_action")
    assert hasattr(window, "export_zip_action")
    assert hasattr(window, "import_existing_machine_action")
    assert hasattr(window, "help_about_action")
    assert window.tabs.tabBar().isVisible() is False
    assert not hasattr(window, "view_advanced_mode_action")


def test_files_experiment_flag_defaults_off(qtbot, tmp_path) -> None:
    settings = _temp_settings(tmp_path)
    window = MainWindow(app_settings=settings)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert window._is_files_experiment_enabled() is False
    assert window.view_files_experiment_action.isChecked() is False
    assert window.app_state_store.snapshot().ui.files_ui_variant == "classic"


def test_files_experiment_toggle_persists_across_restart(qtbot, tmp_path) -> None:
    settings = _temp_settings(tmp_path)
    window = MainWindow(app_settings=settings)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    window._set_files_experiment_enabled(True)
    assert window._is_files_experiment_enabled() is True
    assert window.app_state_store.snapshot().ui.files_ui_variant == "material_v1"
    window.close()

    restarted = MainWindow(app_settings=settings)
    qtbot.addWidget(restarted)
    restarted.show()
    qtbot.waitUntil(lambda: restarted.preset_combo.count() > 0)

    assert restarted._is_files_experiment_enabled() is True
    assert restarted.view_files_experiment_action.isChecked() is True
    assert restarted.app_state_store.snapshot().ui.files_ui_variant == "material_v1"


def test_files_experiment_builder_selected_when_enabled(qtbot, tmp_path) -> None:
    settings = _temp_settings(tmp_path)
    settings.setValue(MainWindow.FILES_EXPERIMENT_SETTING_KEY, True)
    settings.sync()

    window = MainWindow(app_settings=settings)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert window.files_tab.objectName() == "files_tab_material_v1"
    assert hasattr(window, "generated_file_list")
    assert hasattr(window, "file_view_tabs")
    assert hasattr(window, "validation_table")
    assert hasattr(window, "cfg_tools_status_label")
    assert hasattr(window, "files_primary_status_chip")
    assert hasattr(window, "files_blocking_chip")
    assert hasattr(window, "files_warning_chip")
    assert hasattr(window, "files_dirty_chip")


def test_files_experiment_stylesheet_applies_in_dark_and_light(qtbot, tmp_path) -> None:
    settings = _temp_settings(tmp_path)
    settings.setValue(MainWindow.FILES_EXPERIMENT_SETTING_KEY, True)
    settings.sync()

    window = MainWindow(app_settings=settings)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    app = QApplication.instance()
    assert app is not None

    window._apply_theme_mode("dark")
    dark_stylesheet = app.styleSheet()
    assert "#files_tab_material_v1" in dark_stylesheet
    assert "#1f1f1f" in dark_stylesheet

    window._apply_theme_mode("light")
    light_stylesheet = app.styleSheet()
    assert "#files_tab_material_v1" in light_stylesheet
    assert "#f4f6fb" in light_stylesheet


def test_files_experiment_preserves_file_selection_and_raw_form_modes(qtbot, tmp_path) -> None:
    settings = _temp_settings(tmp_path)
    settings.setValue(MainWindow.FILES_EXPERIMENT_SETTING_KEY, True)
    settings.sync()

    window = MainWindow(app_settings=settings)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    _select_first_mainboard(window)

    qtbot.waitUntil(lambda: window.generated_file_list.count() > 0)
    window.generated_file_list.setCurrentRow(0)
    qtbot.waitUntil(lambda: bool(window.file_preview.toPlainText().strip()))

    original_index = window.file_view_tabs.currentIndex()
    window._toggle_raw_form_mode()

    assert window.file_view_tabs.currentIndex() != original_index
    assert window.files_breadcrumbs_label.text().startswith("Path:")
    assert window.files_primary_status_chip.text().strip() != ""


def test_files_experiment_uses_plain_english_validation_status_copy(qtbot, tmp_path) -> None:
    settings = _temp_settings(tmp_path)
    settings.setValue(MainWindow.FILES_EXPERIMENT_SETTING_KEY, True)
    settings.sync()

    window = MainWindow(app_settings=settings)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    _select_first_mainboard(window)
    qtbot.waitUntil(lambda: window.generated_file_list.count() > 0)

    status_text = window.cfg_tools_status_label.text()
    assert status_text.startswith(("Looks good:", "Action needed:", "Heads up:"))


def test_vendor_field_defaults_to_placeholder_and_expected_options(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    labels = [window.vendor_combo.itemText(index) for index in range(window.vendor_combo.count())]
    assert labels == ["None", "custom printer", "Voron"]
    assert window.vendor_combo.currentIndex() == 0
    assert window.vendor_combo.currentText() == "None"


def test_generate_tab_header_row_stays_top_aligned_on_tall_window(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    window._on_shell_route_selected("generate")
    window.resize(1800, 1200)
    qtbot.wait(50)

    tab_height = max(1, window.wizard_tab.height())
    assert window.render_validate_btn.y() < (tab_height // 5)


def test_preset_defaults_to_none_and_switches_for_voron_vendor(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert window.preset_combo.currentData() is None
    assert window.preset_combo.currentText() == "None"

    voron_index = window.vendor_combo.findData("voron")
    assert voron_index >= 0
    window.vendor_combo.setCurrentIndex(voron_index)

    assert window.preset_combo.currentData() == MainWindow.DEFAULT_VORON_PRESET_ID
    assert window.preset_combo.currentText() == "Voron 2.4 350"

    window.vendor_combo.setCurrentIndex(0)
    assert window.preset_combo.currentData() is None
    assert window.preset_combo.currentText() == "None"


def test_mainboard_field_defaults_to_placeholder_and_expected_text(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert window.board_combo.currentIndex() == 0
    assert window.board_combo.currentText() == "Choose your mainboard"


def test_thermistor_fields_default_to_blank(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert window.hotend_thermistor_edit.text() == ""
    assert window.bed_thermistor_edit.text() == ""
    assert window.hotend_thermistor_edit.placeholderText() == "Hotend Thermistor"
    assert window.bed_thermistor_edit.placeholderText() == "Bed Thermistor"


def test_probe_defaults_to_none_option(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    _select_default_voron_preset(window)

    assert window.probe_type_combo.currentText() == "None"


def test_macro_addon_and_led_sections_are_collapsible(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    window.tabs.setCurrentWidget(window.wizard_tab)

    assert window.macros_section_toggle.isChecked() is True
    assert window.addons_section_toggle.isChecked() is True
    assert window.led_section_toggle.isChecked() is False
    assert window.macros_section_content.isVisible() is True
    assert window.addons_section_content.isVisible() is False
    assert window.led_section_content.isVisible() is False

    window.macros_section_toggle.setChecked(False)
    window.addons_section_toggle.setChecked(False)
    window.led_section_toggle.setChecked(True)

    assert window.macros_section_content.isVisible() is False
    assert window.addons_section_content.isVisible() is False
    assert window.led_section_content.isVisible() is True


def test_macro_packs_and_addons_group_is_below_core_hardware(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    window.tabs.setCurrentWidget(window.wizard_tab)

    group_boxes = window.wizard_tab.findChildren(QGroupBox)
    core_group = next(group for group in group_boxes if group.title() == "Core Hardware")
    options_group = next(group for group in group_boxes if group.title() == "Macro Packs")

    assert options_group.y() > core_group.y()
    assert abs(options_group.x() - core_group.x()) <= 4


def test_configuration_package_explorer_and_preview_follow_generated_pack(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert hasattr(window, "wizard_package_file_list")
    assert hasattr(window, "wizard_package_preview")
    assert hasattr(window, "wizard_package_preview_label")
    assert window.wizard_package_file_list.count() == 0

    _select_first_mainboard(window)
    qtbot.waitUntil(lambda: window.current_pack is not None)
    qtbot.waitUntil(lambda: window.wizard_package_file_list.count() > 0)

    printer_row = -1
    for row in range(window.wizard_package_file_list.count()):
        item = window.wizard_package_file_list.item(row)
        if item is not None and item.text() == "printer.cfg":
            printer_row = row
            break

    assert printer_row >= 0
    window.wizard_package_file_list.setCurrentRow(printer_row)

    assert window.wizard_package_preview_label.text() == "Generated: printer.cfg"
    assert "[printer]" in window.wizard_package_preview.toPlainText()


def test_macro_and_addon_checkboxes_update_project(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    macro_checkbox = window.macro_checkboxes["core_maintenance"]
    _select_first_mainboard(window)

    macro_checkbox.setChecked(True)

    project = window._build_project_from_ui()
    assert "core_maintenance" in project.macro_packs
    assert project.addons == []


def test_toolhead_selection_updates_project(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    _select_first_mainboard(window)
    _select_first_can_toolhead_board(window)
    window.toolhead_canbus_uuid_edit.setText("1234abcd5678efgh")

    project = window._build_project_from_ui()
    assert project.toolhead.enabled is True
    assert project.toolhead.board is not None
    assert project.toolhead.canbus_uuid == "1234abcd5678efgh"


def test_toolhead_usb_and_can_board_dropdowns_are_separate(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    _select_default_voron_preset(window)

    assert hasattr(window, "toolhead_can_board_combo")
    assert hasattr(window, "toolhead_usb_board_combo")
    assert window.toolhead_can_board_combo is not window.toolhead_usb_board_combo
    assert window.toolhead_can_board_combo.currentData() is None
    assert window.toolhead_usb_board_combo.currentData() is None

    _select_first_can_toolhead_board(window)
    assert window.toolhead_usb_board_combo.currentData() is None


def test_toolhead_fields_order_and_transport_sorting(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    _select_default_voron_preset(window)

    form_layout = window.toolhead_usb_board_combo.parentWidget().layout()
    usb_row, _ = form_layout.getWidgetPosition(window.toolhead_usb_board_combo)
    can_row, _ = form_layout.getWidgetPosition(window.toolhead_can_board_combo)
    assert usb_row < can_row

    can_labels = [
        window.toolhead_can_board_combo.itemText(index)
        for index in range(1, window.toolhead_can_board_combo.count())
    ]
    usb_labels = [
        window.toolhead_usb_board_combo.itemText(index)
        for index in range(1, window.toolhead_usb_board_combo.count())
    ]
    assert can_labels == sorted(can_labels, key=str.lower)
    assert usb_labels == sorted(usb_labels, key=str.lower)


def test_probe_and_toolhead_ignored_until_populated(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    _select_first_mainboard(window)

    project = window._build_project_from_ui()
    assert project.probe.enabled is False
    assert project.probe.type is None
    assert project.toolhead.enabled is False
    assert project.toolhead.board is None
    assert project.toolhead.canbus_uuid is None

    window.probe_type_combo.setCurrentText("tap")
    _select_first_can_toolhead_board(window)
    window.toolhead_canbus_uuid_edit.setText("1234abcd5678efgh")
    project = window._build_project_from_ui()
    assert project.probe.enabled is True
    assert project.probe.type == "tap"
    assert project.toolhead.enabled is True
    assert project.toolhead.board is not None


def test_led_controls_update_project(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    _select_first_mainboard(window)
    window.led_enabled_checkbox.setChecked(True)
    window.led_pin_edit.setText("PA8")
    window.led_chain_count_spin.setValue(8)
    window.led_color_order_combo.setCurrentText("GRB")
    window.led_initial_blue_spin.setValue(0.1)

    project = window._build_project_from_ui()
    assert project.leds.enabled is True
    assert project.leds.pin == "PA8"
    assert project.leds.chain_count == 8
    assert project.leds.color_order == "GRB"
    assert project.leds.initial_blue == 0.1


def test_live_conflict_toast_appears_and_conflicts_clear(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    _select_first_mainboard(window)
    qtbot.waitUntil(lambda: not window.current_report.has_blocking)
    assert not hasattr(window, "conflict_alert_label")

    _select_first_can_toolhead_board(window)

    qtbot.waitUntil(lambda: window.current_report.has_blocking)
    qtbot.waitUntil(
        lambda: window.toast_notification.isVisible() and "Please fix" in window.toast_notification.text()
    )

    if window.toolhead_can_board_combo.currentData() is not None:
        window.toolhead_canbus_uuid_edit.setText("1234abcd5678efgh")

    qtbot.waitUntil(lambda: not window.current_report.has_blocking)
    assert window._last_blocking_alert_snapshot == ()


def test_footer_device_health_indicator_updates_from_ssh_connect(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert "Disconnected" in window.device_health_icon.toolTip()

    window.ssh_host_edit.setText("192.168.1.20")
    window.ssh_username_edit.setText("pi")
    window.ssh_connection_name_edit.setText("Voron Lab")
    assert window.tools_connect_action.text() == "Current SSH Fields"

    window.ssh_service = FakeConnectionService(ok=True, output="ok")
    window.tools_connect_action.trigger()
    assert "Connected" in window.device_health_icon.toolTip()
    assert "Voron Lab" in window.manage_connected_printer_label.text()
    assert "Voron Lab" in window.modify_connected_printer_label.text()

    window.ssh_service = FakeConnectionService(ok=False, output="auth failed")
    window.tools_connect_action.trigger()
    assert "Disconnected" in window.device_health_icon.toolTip()
    assert "No active SSH connection." in window.manage_connected_printer_label.text()
    assert "No active SSH connection." in window.modify_connected_printer_label.text()


def test_files_sections_collapsed_by_default_and_issue_notice(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    _select_first_mainboard(window)
    qtbot.waitUntil(lambda: not window.current_report.has_blocking)

    assert window.overrides_section_toggle.isChecked() is False
    assert window.validation_section_toggle.isChecked() is False
    assert not hasattr(window, "files_validation_notice_label")

    window._update_validation_issue_notification(0, 2)
    qtbot.waitUntil(lambda: window.toast_notification.isVisible() and "Heads up:" in window.toast_notification.text())

    assert "we found 2 warnings" in window.toast_notification.text()
    assert "Validation Findings (" in window.validation_section_toggle.text()


def test_console_moved_to_tools_window_and_removed_from_workflow_tabs(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert not hasattr(window, "ssh_log_section_toggle")
    assert not hasattr(window, "manage_log_section_toggle")
    assert not hasattr(window, "modify_log_section_toggle")

    assert window.active_console_window is not None
    assert window.active_console_window.isVisible() is False
    window.tools_active_console_action.trigger()
    assert window.active_console_window.isVisible() is True

    window._append_ssh_log("ssh log line")
    window._append_manage_log("manage log line")
    window._append_modify_log("modify log line")
    assert "ssh log line" in window.ssh_log.toPlainText()
    assert "manage log line" in window.manage_log.toPlainText()
    assert "modify log line" in window.modify_log.toPlainText()
    assert "[SSH]" in window.console_activity_log.toPlainText()
    assert "[MANAGE]" in window.console_activity_log.toPlainText()
    assert "[MODIFY]" in window.console_activity_log.toPlainText()


def test_main_tab_routes_without_resetting_configuration(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    window.dimension_x.setValue(333)
    window.tabs.setCurrentWidget(window.main_tab)

    window.main_new_firmware_btn.click()
    assert window.tabs.currentWidget() is window.wizard_tab
    assert window.dimension_x.value() == 333

    window.tabs.setCurrentWidget(window.main_tab)
    window.main_modify_existing_btn.click()
    assert window.tabs.currentWidget() is window.modify_existing_tab

    window.tabs.setCurrentWidget(window.main_tab)
    window.main_connect_manage_btn.click()
    assert window.printer_connection_window is not None
    assert window.printer_connection_window.isVisible()

    window.tabs.setCurrentWidget(window.main_tab)
    window.main_about_btn.click()
    assert window.about_window is not None
    assert window.about_window.isVisible()


def test_main_tab_import_existing_machine_loads_review(qtbot, monkeypatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    fixture_root = Path(__file__).resolve().parent / "fixtures" / "existing_machine_sample"
    monkeypatch.setattr(
        window,
        "_choose_import_source",
        lambda: (str(fixture_root), "folder"),
    )

    window.import_existing_machine_action.trigger()

    assert window.tabs.currentWidget() is window.files_tab
    assert window.current_import_profile is not None
    assert window.import_review_table.rowCount() > 0
    assert window.generated_file_list.count() > 0


def test_import_apply_selected_updates_configuration_controls(qtbot, monkeypatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    fixture_root = Path(__file__).resolve().parent / "fixtures" / "existing_machine_sample"
    monkeypatch.setattr(
        window,
        "_choose_import_source",
        lambda: (str(fixture_root), "folder"),
    )
    window.import_existing_machine_action.trigger()

    window._select_high_confidence_import_suggestions()
    window._apply_selected_import_suggestions()

    assert window.dimension_x.value() == 350
    assert window.dimension_y.value() == 350
    assert (
        window.toolhead_can_board_combo.currentData() is not None
        or window.toolhead_usb_board_combo.currentData() is not None
    )
    assert window.toolhead_canbus_uuid_edit.text() == "abcdef1234567890"


def test_machine_profile_save_and_load_restores_import_state(qtbot, monkeypatch, tmp_path) -> None:
    window = MainWindow()
    window.saved_machine_profile_service.storage_path = tmp_path / "machine_profiles.json"
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    fixture_root = Path(__file__).resolve().parent / "fixtures" / "existing_machine_sample"
    monkeypatch.setattr(
        window,
        "_choose_import_source",
        lambda: (str(fixture_root), "folder"),
    )
    window.import_existing_machine_action.trigger()

    window.machine_profile_name_edit.setText("Fixture Import")
    window._save_current_machine_profile()
    assert window.machine_profile_combo.findText("Fixture Import") >= 0

    window.current_import_profile = None
    window.imported_file_map = {}
    window.import_review_table.setRowCount(0)
    window.machine_profile_combo.setCurrentText("Fixture Import")
    window._load_selected_machine_profile()

    assert window.current_import_profile is not None
    assert window.generated_file_list.count() > 0
    assert window.import_review_table.rowCount() > 0


def test_modify_existing_workflow_happy_path(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    fake_service = FakeModifyWorkflowService()
    window.ssh_service = fake_service
    window.ssh_host_edit.setText("192.168.1.20")
    window.ssh_username_edit.setText("pi")
    window.modify_remote_cfg_path_edit.setText("~/printer_data/config/printer.cfg")

    window._modify_connect()
    assert "Connected" in window.device_health_icon.toolTip()
    assert "connected" in window.modify_log.toPlainText().lower()

    window._modify_open_remote_cfg()
    assert "[printer]" in window.modify_editor.toPlainText()

    window._modify_refactor_current_file()
    assert "kinematics: corexy" in window.modify_editor.toPlainText()

    window._modify_validate_current_file()
    assert "validation passed" in window.modify_status_label.text().lower()

    window._modify_upload_current_file()
    assert fake_service.backup_calls
    assert fake_service.saved is not None
    assert fake_service.saved[0].endswith("printer.cfg")

    window._modify_test_restart()
    assert fake_service.command_calls
    assert "restart ok" in window.modify_log.toPlainText().lower()


def test_modify_existing_failure_paths_log_and_error(qtbot, monkeypatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(window, "_show_error", lambda title, msg: errors.append((title, msg)))

    fake_service = FakeModifyWorkflowService()
    window.ssh_service = fake_service
    window.ssh_host_edit.setText("192.168.1.20")
    window.ssh_username_edit.setText("pi")
    window.modify_remote_cfg_path_edit.setText("~/printer_data/config/printer.cfg")

    fake_service.fail_fetch = True
    window._modify_open_remote_cfg()
    assert any("fetch failed" in message for _title, message in errors)

    fake_service.fail_fetch = False
    window._modify_open_remote_cfg()
    window.modify_editor.clear()
    window._modify_upload_current_file()
    assert any("empty" in message.lower() for _title, message in errors)

    window.modify_editor.setPlainText("[printer]\nkinematics: corexy\nmax_velocity: 250\n")
    fake_service.fail_write = True
    window._modify_upload_current_file()
    assert any("write failed" in message for _title, message in errors)

    fake_service.fail_write = False
    fake_service.fail_restart = True
    window._modify_test_restart()
    assert any("restart failed" in message for _title, message in errors)
    assert "failed" in window.modify_log.toPlainText().lower()


def test_successful_ssh_connection_saves_named_profile(qtbot, tmp_path) -> None:
    saved_connections = SavedConnectionService(storage_path=tmp_path / "saved_connections.json")
    window = MainWindow(saved_connection_service=saved_connections)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    window.ssh_connection_name_edit.setText("My Voron")
    window.ssh_host_edit.setText("192.168.1.20")
    window.ssh_username_edit.setText("pi")
    window.ssh_password_edit.setText("s3cr3t")
    window.ssh_service = FakeConnectionService(ok=True, output="ok")
    window._connect_ssh_to_host()

    names = [
        window.ssh_saved_connection_combo.itemText(index)
        for index in range(window.ssh_saved_connection_combo.count())
    ]
    assert "My Voron" in names

    loaded = saved_connections.load("My Voron")
    assert loaded is not None
    assert loaded["host"] == "192.168.1.20"
    assert loaded["username"] == "pi"
    assert loaded["password"] == "s3cr3t"


def test_load_saved_profile_populates_ssh_fields(qtbot, tmp_path) -> None:
    saved_connections = SavedConnectionService(storage_path=tmp_path / "saved_connections.json")
    saved_connections.save(
        "Shop Printer",
        {
            "host": "printer.local",
            "port": 2222,
            "username": "klipper",
            "password": "topsecret",
            "key_path": "C:/keys/printer_ed25519",
            "remote_dir": "~/printer_data/config",
            "remote_file": "~/printer_data/config/printer.cfg",
        },
    )

    window = MainWindow(saved_connection_service=saved_connections)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    window.ssh_saved_connection_combo.setCurrentText("Shop Printer")
    window._load_selected_saved_connection()

    assert window.ssh_connection_name_edit.text() == "Shop Printer"
    assert window.ssh_host_edit.text() == "printer.local"
    assert window.ssh_port_spin.value() == 2222
    assert window.ssh_username_edit.text() == "klipper"
    assert window.ssh_password_edit.text() == "topsecret"
    assert window.ssh_key_path_edit.text() == "C:/keys/printer_ed25519"


def test_tools_connect_menu_connects_selected_saved_profile(qtbot, tmp_path) -> None:
    saved_connections = SavedConnectionService(storage_path=tmp_path / "saved_connections.json")
    saved_connections.save(
        "Shop Printer",
        {
            "host": "printer.local",
            "port": 2222,
            "username": "klipper",
            "password": "topsecret",
            "key_path": "",
            "remote_dir": "~/printer_data/config",
            "remote_file": "~/printer_data/config/printer.cfg",
        },
    )

    window = MainWindow(saved_connection_service=saved_connections)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    window.ssh_service = FakeConnectionService(ok=True, output="ok")
    window._refresh_tools_connect_menu()

    profile_action = next(
        action for action in window.tools_connect_menu.actions() if action.text() == "Shop Printer"
    )
    profile_action.trigger()

    assert window.ssh_connection_name_edit.text() == "Shop Printer"
    assert window.ssh_host_edit.text() == "printer.local"
    assert window.ssh_port_spin.value() == 2222
    assert window.ssh_username_edit.text() == "klipper"
    assert "Connected" in window.device_health_icon.toolTip()
    assert "Shop Printer" in window.manage_connected_printer_label.text()


def test_startup_auto_connect_uses_saved_profile_async(qtbot, tmp_path) -> None:
    saved_connections = SavedConnectionService(storage_path=tmp_path / "saved_connections.json")
    saved_connections.save(
        "Startup Printer",
        {
            "host": "192.168.1.20",
            "port": 22,
            "username": "pi",
            "password": "secret",
            "key_path": "",
            "remote_dir": "~/printer_data/config",
            "remote_file": "~/printer_data/config/printer.cfg",
        },
    )

    window = MainWindow(
        saved_connection_service=saved_connections,
        auto_connect_on_launch=True,
    )
    qtbot.addWidget(window)
    window.ssh_service = FakeConnectionService(ok=True, output="ok", delay_seconds=0.25)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    qtbot.waitUntil(lambda: window.auto_connect_in_progress, timeout=4000)
    qtbot.waitUntil(lambda: window.device_connected, timeout=6000)

    assert window.ssh_connection_name_edit.text() == "Startup Printer"
    assert "Connected" in window.device_health_icon.toolTip()
    assert "Startup Printer" in window.manage_connected_printer_label.text()


def test_startup_auto_connect_skips_when_multiple_profiles_and_no_default(qtbot, tmp_path) -> None:
    saved_connections = SavedConnectionService(storage_path=tmp_path / "saved_connections.json")
    saved_connections.save(
        "Alpha Printer",
        {
            "host": "192.168.1.10",
            "port": 22,
            "username": "pi",
            "password": "secret",
            "key_path": "",
            "remote_dir": "~/printer_data/config",
            "remote_file": "~/printer_data/config/printer.cfg",
        },
    )
    saved_connections.save(
        "Bravo Printer",
        {
            "host": "192.168.1.11",
            "port": 22,
            "username": "pi",
            "password": "secret",
            "key_path": "",
            "remote_dir": "~/printer_data/config",
            "remote_file": "~/printer_data/config/printer.cfg",
        },
    )

    settings = _temp_settings(tmp_path, "auto_connect_multi.ini")
    window = MainWindow(
        saved_connection_service=saved_connections,
        app_settings=settings,
        auto_connect_on_launch=True,
    )
    qtbot.addWidget(window)
    window.ssh_service = FakeConnectionService(ok=True, output="ok", delay_seconds=0.2)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    qtbot.wait(900)

    assert window.device_connected is False
    assert "multiple saved connections found" in window.ssh_log.toPlainText().lower()


def test_startup_auto_connect_uses_default_profile_when_multiple_saved(qtbot, tmp_path) -> None:
    saved_connections = SavedConnectionService(storage_path=tmp_path / "saved_connections.json")
    saved_connections.save(
        "Alpha Printer",
        {
            "host": "192.168.1.10",
            "port": 22,
            "username": "pi",
            "password": "secret",
            "key_path": "",
            "remote_dir": "~/printer_data/config",
            "remote_file": "~/printer_data/config/printer.cfg",
        },
    )
    saved_connections.save(
        "Bravo Printer",
        {
            "host": "192.168.1.44",
            "port": 22,
            "username": "pi",
            "password": "secret",
            "key_path": "",
            "remote_dir": "~/printer_data/config",
            "remote_file": "~/printer_data/config/printer.cfg",
        },
    )

    saved_connections.set_default_connection_name("Bravo Printer")

    window = MainWindow(
        saved_connection_service=saved_connections,
        auto_connect_on_launch=True,
    )
    qtbot.addWidget(window)
    window.ssh_service = FakeConnectionService(ok=True, output="ok", delay_seconds=0.2)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    qtbot.waitUntil(lambda: window.device_connected, timeout=6000)

    assert window.ssh_connection_name_edit.text() == "Bravo Printer"
    assert window.ssh_host_edit.text() == "192.168.1.44"


def test_startup_auto_connect_can_be_disabled(qtbot, tmp_path) -> None:
    saved_connections = SavedConnectionService(storage_path=tmp_path / "saved_connections.json")
    saved_connections.save(
        "Startup Printer",
        {
            "host": "192.168.1.20",
            "port": 22,
            "username": "pi",
            "password": "secret",
            "key_path": "",
            "remote_dir": "~/printer_data/config",
            "remote_file": "~/printer_data/config/printer.cfg",
        },
    )

    saved_connections.set_auto_connect_enabled(False)

    window = MainWindow(
        saved_connection_service=saved_connections,
        auto_connect_on_launch=True,
    )
    qtbot.addWidget(window)
    window.ssh_service = FakeConnectionService(ok=True, output="ok", delay_seconds=0.2)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    qtbot.wait(900)

    assert window.device_connected is False
    assert window.ssh_auto_connect_checkbox.isChecked() is False


def test_set_default_connection_from_selection_updates_setting(qtbot, tmp_path) -> None:
    saved_connections = SavedConnectionService(storage_path=tmp_path / "saved_connections.json")
    saved_connections.save(
        "Alpha Printer",
        {
            "host": "192.168.1.10",
            "port": 22,
            "username": "pi",
            "password": "secret",
            "key_path": "",
            "remote_dir": "~/printer_data/config",
            "remote_file": "~/printer_data/config/printer.cfg",
        },
    )
    saved_connections.save(
        "Bravo Printer",
        {
            "host": "192.168.1.11",
            "port": 22,
            "username": "pi",
            "password": "secret",
            "key_path": "",
            "remote_dir": "~/printer_data/config",
            "remote_file": "~/printer_data/config/printer.cfg",
        },
    )

    settings = _temp_settings(tmp_path, "default_select.ini")
    window = MainWindow(saved_connection_service=saved_connections, app_settings=settings)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    window.ssh_saved_connection_combo.setCurrentText("Bravo Printer")
    window._set_default_saved_connection_from_selection()

    assert window.default_ssh_connection_name == "Bravo Printer"
    assert "Bravo Printer" in window.ssh_default_connection_label.text()
    assert saved_connections.get_default_connection_name() == "Bravo Printer"


def test_guided_component_setup_creates_mainboard_bundle(qtbot, monkeypatch, tmp_path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    mainboard_spec = {
        "component_type": "mainboard",
        "id": "bundle_board",
        "payload": {
            "id": "bundle_board",
            "label": "Bundle Board",
            "mcu": "stm32f446xx",
            "serial_hint": "/dev/serial/by-id/usb-Bundle_Board",
            "pins": {
                "stepper_x_step": "PB13",
                "stepper_x_dir": "PB12",
            },
            "layout": {"Stepper Drivers": ["X", "Y"]},
        },
    }
    monkeypatch.setattr(
        window,
        "_run_guided_component_setup_wizard",
        lambda: (tmp_path, mainboard_spec),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.information",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(window, "_refresh_bundle_backed_component_options", lambda: None)

    window._open_guided_component_setup()

    bundle_file = tmp_path / "boards" / "bundle_board.json"
    assert bundle_file.exists()
    payload = json.loads(bundle_file.read_text(encoding="utf-8"))
    assert payload["id"] == "bundle_board"
    assert payload["label"] == "Bundle Board"
    assert payload["mcu"] == "stm32f446xx"


def test_guided_component_setup_rejects_addon_bundle_in_disabled_mode(
    qtbot,
    monkeypatch,
    tmp_path,
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    addon_spec = {
        "component_type": "addon",
        "id": "chamber_plus",
        "payload": {
            "id": "chamber_plus",
            "label": "Chamber Plus",
            "template": "addons/chamber_plus.cfg.j2",
            "description": "Addon from guided wizard.",
            "multi_material": False,
            "recommends_toolhead": False,
            "supported_families": ["voron"],
        },
        "template_rel": "addons/chamber_plus.cfg.j2",
        "template_content": "[gcode_macro CHAMBER_PLUS]\ngcode:\n  RESPOND MSG=\"ok\"\n",
    }
    monkeypatch.setattr(
        window,
        "_run_guided_component_setup_wizard",
        lambda: (tmp_path, addon_spec),
    )
    monkeypatch.setattr(window, "_refresh_bundle_backed_component_options", lambda: None)
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(window, "_show_error", lambda title, msg: errors.append((title, msg)))

    window._open_guided_component_setup()

    assert errors
    assert "Add-on bundle creation is disabled" in errors[0][1]


def test_explore_config_requires_connected_printer(qtbot, monkeypatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(window, "_show_error", lambda title, msg: errors.append((title, msg)))

    window._explore_connected_config_directory()
    assert errors
    assert "Connect to a printer first" in errors[0][1]


def test_explore_connected_config_directory_routes_to_manage_and_refreshes(qtbot, monkeypatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    window.ssh_host_edit.setText("192.168.1.20")
    window.ssh_username_edit.setText("pi")
    window.ssh_service = FakeConnectionService(ok=True, output="ok")
    window._connect_ssh_to_host()
    assert window.device_connected is True

    calls: list[str | None] = []
    monkeypatch.setattr(window, "_manage_refresh_files", lambda target_dir=None: calls.append(target_dir))

    window._explore_connected_config_directory()

    assert window.tabs.currentWidget() is window.manage_printer_tab
    assert window.manage_host_edit.text() == "192.168.1.20"
    assert calls
    assert calls[0] == window.ssh_remote_dir_edit.text().strip()


def test_about_window_contains_quote_and_creator_icon(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    window.help_about_action.trigger()
    assert window.about_window is not None
    assert window.about_window.isVisible()
    assert "accessibility" in window.about_quote_label.text().lower()
    pixmap = window.about_creator_icon_label.pixmap()
    has_pixmap = pixmap is not None and not pixmap.isNull()
    has_fallback = bool(window.about_creator_icon_label.text().strip())
    assert has_pixmap or has_fallback


def test_persistent_preview_removed_from_main_layout(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert not hasattr(window, "preview_panel")
    assert not hasattr(window, "preview_toggle_action")


def test_command_bar_printer_and_tools_actions_match_expected_labels(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert window.printer_upload_action.text() == "Upload Current"
    assert window.printer_connect_action.text() == "Connection Window..."
    assert window.tools_guided_component_setup_action.text() == "Guided Component Setup..."
    assert window.tools_connect_menu.title() == "Connect"
    assert window.tools_connect_action.text() == "Current SSH Fields"
    assert window.tools_open_remote_action.text() == "Open Remote Config"
    assert window.tools_explore_config_action.text() == "Explore Config Directory"
    assert window.tools_deploy_action.text() == "Deploy Generated Pack"
    assert window.tools_active_console_action.text() == "Active Console"
    assert window.tools_printer_discovery_action.text() == "Scan For Printers..."
    assert window.tools_scan_printers_action.text() == "Scan For Printers..."
    assert not hasattr(window, "tools_use_selected_host_action")


def test_command_bar_primary_shortcuts_are_wired(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert window.printer_connect_action.shortcut().toString() == "Ctrl+Shift+C"
    assert window.configuration_validate_action.shortcut().toString() == "Ctrl+Shift+V"
    assert window.configuration_compile_action.shortcut().toString() == "Ctrl+Shift+G"
    assert window.printer_upload_action.shortcut().toString() == "Ctrl+Shift+U"
    assert window.printer_restart_klipper_action.shortcut().toString() == "Ctrl+Shift+R"


def test_printer_menu_actions_are_disabled_without_active_connection(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    _select_first_mainboard(window)
    qtbot.waitUntil(lambda: not window.current_report.has_blocking)

    window.ssh_host_edit.setText("192.168.1.20")
    window.ssh_username_edit.setText("pi")
    window._set_device_connection_health(False, "Disconnected for test")
    window._update_action_enablement()

    assert window.printer_upload_action.isEnabled() is False
    assert window.printer_restart_klipper_action.isEnabled() is False
    assert window.printer_restart_host_action.isEnabled() is False

    window._set_device_connection_health(True, "Connected for test")
    window._update_action_enablement()

    assert window.printer_upload_action.isEnabled() is True
    assert window.printer_restart_klipper_action.isEnabled() is True
    assert window.printer_restart_host_action.isEnabled() is True


def test_left_nav_has_no_legacy_route(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    labels = [window.left_nav_scaffold.item(index).text() for index in range(window.left_nav_scaffold.count())]
    assert "Legacy" not in labels
    assert "Home" in labels
    assert "Connect" not in labels
    assert "Edit Config" not in labels
    assert "Printers" in labels


def test_printers_route_opens_setup_when_not_configured(qtbot, monkeypatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    window.ssh_host_edit.clear()
    window.ssh_username_edit.clear()

    calls: list[str] = []
    monkeypatch.setattr(
        window,
        "_open_printer_connection_window",
        lambda **_kwargs: calls.append("setup"),
    )
    monkeypatch.setattr(window, "_manage_open_control_window", lambda: calls.append("webview"))

    window._on_shell_route_selected("printers")

    assert calls == ["setup"]
    assert window.app_state_store.snapshot().ui.active_route == "printers"


def test_printers_route_opens_webview_when_configured(qtbot, monkeypatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    window.ssh_host_edit.setText("192.168.1.20")
    window.ssh_username_edit.setText("pi")

    calls: list[str] = []
    monkeypatch.setattr(
        window,
        "_open_printer_connection_window",
        lambda **_kwargs: calls.append("setup"),
    )
    monkeypatch.setattr(window, "_manage_open_control_window", lambda: calls.append("webview"))

    window._on_shell_route_selected("printers")

    assert calls == ["webview"]
    assert window.app_state_store.snapshot().ui.active_route == "printers"


def test_duplicate_primary_buttons_removed_from_views(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert not hasattr(window, "modify_validate_btn")
    assert not hasattr(window, "modify_upload_btn")
    assert not hasattr(window, "modify_test_restart_btn")
    assert not hasattr(window, "validate_cfg_btn")
    assert not hasattr(window, "manage_validate_file_btn")


def test_command_bar_primary_actions_dispatch_by_route(qtbot, monkeypatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    calls: list[str] = []
    monkeypatch.setattr(window, "_validate_current_cfg_file", lambda: calls.append("files_validate"))
    monkeypatch.setattr(window, "_modify_validate_current_file", lambda: calls.append("modify_validate"))
    monkeypatch.setattr(window, "_manage_validate_current_file", lambda: calls.append("manage_validate"))
    monkeypatch.setattr(window, "_deploy_generated_pack", lambda: calls.append("deploy_generated"))
    monkeypatch.setattr(window, "_modify_upload_current_file", lambda: calls.append("modify_upload"))
    monkeypatch.setattr(window, "_manage_save_current_file", lambda: calls.append("manage_save"))
    monkeypatch.setattr(window, "_restart_klipper_service", lambda: calls.append("restart_klipper"))
    monkeypatch.setattr(window, "_modify_test_restart", lambda: calls.append("modify_restart"))

    window._on_shell_route_selected("edit_config")
    window._validate_current_context()
    window._upload_current_context()
    window._restart_current_context()

    window._on_shell_route_selected("backups")
    window._validate_current_context()
    window._upload_current_context()
    window._restart_current_context()

    window._on_shell_route_selected("generate")
    window._validate_current_context()
    window._upload_current_context()
    window._restart_current_context()

    assert calls == [
        "modify_validate",
        "modify_upload",
        "modify_restart",
        "manage_validate",
        "manage_save",
        "restart_klipper",
        "files_validate",
        "deploy_generated",
        "restart_klipper",
    ]


def test_machine_attribute_and_addon_detail_views_update_in_configuration(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    _select_first_mainboard(window)
    qtbot.waitUntil(lambda: window.current_project is not None)

    assert hasattr(window, "machine_attr_mcu_view")
    assert hasattr(window, "machine_attr_motion_view")
    assert hasattr(window, "machine_attr_probe_view")
    assert hasattr(window, "machine_attr_thermal_view")
    assert hasattr(window, "addon_package_details_view")

    assert window.machine_attr_mcu_view.toPlainText().strip() != ""
    assert "temporarily disabled" in window.addon_package_details_view.toPlainText().lower()

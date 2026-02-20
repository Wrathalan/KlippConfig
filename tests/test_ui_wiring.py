from app.ui.main_window import MainWindow
from app.services.saved_connections import SavedConnectionService
from app.services.ssh_deploy import SSHDeployError


class FakeConnectionService:
    def __init__(self, ok: bool, output: str) -> None:
        self.ok = ok
        self.output = output

    def test_connection(self, **_kwargs):
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
        "SSH",
        "Modify Existing",
        "Manage Printer",
        "About",
    ]
    assert "Advanced" not in labels
    assert "Validation" not in labels
    assert "Export" not in labels
    assert "Files" in labels
    assert "About" in labels
    assert window.files_tab.isAncestorOf(window.export_folder_btn)
    assert window.files_tab.isAncestorOf(window.export_zip_btn)


def test_macro_and_addon_checkboxes_update_project(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    macro_checkbox = window.macro_checkboxes["core_maintenance"]
    addon_checkbox = window.addon_checkboxes["filament_buffer"]

    macro_checkbox.setChecked(True)
    if addon_checkbox.isEnabled():
        addon_checkbox.setChecked(True)

    project = window._build_project_from_ui()
    assert "core_maintenance" in project.macro_packs
    if addon_checkbox.isEnabled():
        assert "filament_buffer" in project.addons


def test_toolhead_checkbox_updates_project(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    window.toolhead_enabled_checkbox.setChecked(True)

    if window.toolhead_board_combo.count() > 1:
        window.toolhead_board_combo.setCurrentIndex(1)
    window.toolhead_canbus_uuid_edit.setText("1234abcd5678efgh")

    project = window._build_project_from_ui()
    assert project.toolhead.enabled is True
    assert project.toolhead.board is not None
    assert project.toolhead.canbus_uuid == "1234abcd5678efgh"


def test_led_controls_update_project(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
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


def test_live_conflict_alert_appears_and_clears(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    window.toolhead_enabled_checkbox.setChecked(True)

    qtbot.waitUntil(lambda: window.current_report.has_blocking)
    assert window.conflict_alert_label.isVisible()
    assert "Blocking conflicts" in window.conflict_alert_label.text()

    if window.toolhead_board_combo.count() > 1:
        window.toolhead_board_combo.setCurrentIndex(1)
        window.toolhead_canbus_uuid_edit.setText("1234abcd5678efgh")
    else:
        window.toolhead_enabled_checkbox.setChecked(False)

    qtbot.waitUntil(lambda: not window.current_report.has_blocking)
    assert not window.conflict_alert_label.isVisible()


def test_footer_device_health_indicator_updates_from_ssh_connect(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert "Disconnected" in window.device_health_icon.toolTip()

    window.ssh_host_edit.setText("192.168.1.20")
    window.ssh_username_edit.setText("pi")
    window.ssh_connection_name_edit.setText("Voron Lab")
    assert window.ssh_connect_btn.text() == "Connect"

    window.ssh_service = FakeConnectionService(ok=True, output="ok")
    window._connect_ssh_to_host()
    assert "Connected" in window.device_health_icon.toolTip()
    assert "Voron Lab" in window.manage_connected_printer_label.text()
    assert "Voron Lab" in window.modify_connected_printer_label.text()

    window.ssh_service = FakeConnectionService(ok=False, output="auth failed")
    window._connect_ssh_to_host()
    assert "Disconnected" in window.device_health_icon.toolTip()
    assert "No active SSH connection." in window.manage_connected_printer_label.text()
    assert "No active SSH connection." in window.modify_connected_printer_label.text()


def test_files_sections_collapsed_by_default_and_issue_notice(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert window.overrides_section_toggle.isChecked() is False
    assert window.validation_section_toggle.isChecked() is False
    assert window.files_validation_notice_label.isHidden() is True

    window.toolhead_enabled_checkbox.setChecked(True)
    qtbot.waitUntil(lambda: window.current_report.has_blocking)

    assert window.files_validation_notice_label.isHidden() is False
    assert "Validation unresolved" in window.files_validation_notice_label.text()
    assert "Validation Findings (" in window.validation_section_toggle.text()


def test_console_logs_collapsed_by_default_on_ssh_and_manage_tabs(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert window.ssh_log_section_toggle.isChecked() is False
    assert window.manage_log_section_toggle.isChecked() is False
    assert window.modify_log_section_toggle.isChecked() is False
    assert window.ssh_log_section_content.isVisible() is False
    assert window.manage_log_section_content.isVisible() is False
    assert window.modify_log_section_content.isVisible() is False

    window._append_ssh_log("ssh log line")
    window._append_manage_log("manage log line")
    window._append_modify_log("modify log line")
    assert "ssh log line" in window.ssh_log.toPlainText()
    assert "manage log line" in window.manage_log.toPlainText()
    assert "modify log line" in window.modify_log.toPlainText()


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
    assert window.tabs.currentWidget() is window.live_deploy_tab

    window.tabs.setCurrentWidget(window.main_tab)
    window.main_about_btn.click()
    assert window.tabs.currentWidget() is window.about_tab


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


def test_about_tab_contains_quote_and_creator_icon(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert "accessibility" in window.about_quote_label.text().lower()
    pixmap = window.about_creator_icon_label.pixmap()
    has_pixmap = pixmap is not None and not pixmap.isNull()
    has_fallback = bool(window.about_creator_icon_label.text().strip())
    assert has_pixmap or has_fallback

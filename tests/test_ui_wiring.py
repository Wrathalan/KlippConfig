from app.ui.main_window import MainWindow


class FakeConnectionService:
    def __init__(self, ok: bool, output: str) -> None:
        self.ok = ok
        self.output = output

    def test_connection(self, **_kwargs):
        return self.ok, self.output


def test_tabs_hide_advanced_and_keep_files(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    labels = [window.tabs.tabText(index) for index in range(window.tabs.count())]
    assert "Advanced" not in labels
    assert "Validation" not in labels
    assert "Export" not in labels
    assert "Files" in labels
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


def test_footer_device_health_indicator_updates_from_ssh_test(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    assert "Disconnected" in window.device_health_icon.toolTip()

    window.ssh_host_edit.setText("192.168.1.20")
    window.ssh_username_edit.setText("pi")

    window.ssh_service = FakeConnectionService(ok=True, output="ok")
    window._test_ssh_connection()
    assert "Connected" in window.device_health_icon.toolTip()

    window.ssh_service = FakeConnectionService(ok=False, output="auth failed")
    window._test_ssh_connection()
    assert "Disconnected" in window.device_health_icon.toolTip()


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

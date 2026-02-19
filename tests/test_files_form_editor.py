from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog

from app.ui.main_window import MainWindow


def test_generated_cfg_file_builds_form_and_applies_changes(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    qtbot.waitUntil(lambda: window.current_pack is not None)

    items = window.generated_file_list.findItems("printer.cfg", Qt.MatchFlag.MatchExactly)
    assert items
    window.generated_file_list.setCurrentItem(items[0])
    window._show_selected_generated_file()

    assert window.apply_form_btn.isEnabled()
    target = next((row for row in window.cfg_form_editors if row["key"] == "max_velocity"), None)
    assert target is not None

    editor = target["editor"]
    editor.setText("123")
    window._apply_cfg_form_changes()

    assert "max_velocity: 123" in window.file_preview.toPlainText()
    assert window.current_pack is not None
    assert "max_velocity: 123" in window.current_pack.files["printer.cfg"]


def test_local_cfg_file_builds_form_and_updates_preview(qtbot, monkeypatch, tmp_path) -> None:
    cfg_path = tmp_path / "sample.cfg"
    cfg_path.write_text(
        "[printer]\n"
        "max_velocity: 250\n"
        "max_accel: 3000\n"
        "\n"
        "[heater_bed]\n"
        "max_temp: 110\n",
        encoding="utf-8",
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(cfg_path), "Klipper config (*.cfg)"),
    )
    window._open_local_cfg_file()

    assert str(cfg_path) in window.preview_path_label.text()
    target = next((row for row in window.cfg_form_editors if row["key"] == "max_temp"), None)
    assert target is not None

    editor = target["editor"]
    editor.setText("120")
    window._apply_cfg_form_changes()
    assert "max_temp: 120" in window.file_preview.toPlainText()

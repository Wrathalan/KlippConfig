from __future__ import annotations

from PySide6.QtCore import QSettings

from app.services.ui_scaling import UIScalingService
from app.ui.main_window import MainWindow


def _scaling_service(tmp_path, file_name: str = "ui-scale-menu.ini") -> UIScalingService:
    settings = QSettings(str(tmp_path / file_name), QSettings.Format.IniFormat)
    settings.clear()
    settings.sync()
    return UIScalingService(settings=settings)


def test_selecting_ui_scale_action_persists_choice(qtbot, tmp_path) -> None:
    service = _scaling_service(tmp_path)
    window = MainWindow(ui_scaling_service=service, active_scale_mode=service.load_mode())
    qtbot.addWidget(window)
    window.show()

    target = window.ui_scale_actions["90"]
    target.trigger()
    qtbot.wait(10)

    assert target.isChecked()
    assert service.load_mode() == "90"


def test_recreated_window_restores_saved_scale_action(qtbot, tmp_path) -> None:
    service = _scaling_service(tmp_path, "restore.ini")
    first = MainWindow(ui_scaling_service=service, active_scale_mode=service.load_mode())
    qtbot.addWidget(first)
    first.show()

    first.ui_scale_actions["90"].trigger()
    qtbot.wait(10)
    first.close()

    second = MainWindow(ui_scaling_service=service, active_scale_mode=service.load_mode())
    qtbot.addWidget(second)
    second.show()

    assert second.ui_scale_actions["90"].isChecked()


def test_invalid_saved_scale_mode_falls_back_to_auto(qtbot, tmp_path) -> None:
    settings = QSettings(str(tmp_path / "invalid.ini"), QSettings.Format.IniFormat)
    settings.setValue("ui/scale_mode", "bad-value")
    settings.sync()
    service = UIScalingService(settings=settings)

    window = MainWindow(ui_scaling_service=service, active_scale_mode=service.load_mode())
    qtbot.addWidget(window)
    window.show()

    assert service.load_mode() == "auto"
    assert window.ui_scale_actions["auto"].isChecked()

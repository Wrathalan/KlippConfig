from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from app.services.paths import icon_path
from app.services.ui_scaling import UIScalingService
from app.ui.main_window import MainWindow


def main() -> int:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    icon_file = icon_path()
    if icon_file.exists():
        app_icon = QIcon(str(icon_file))
        app.setWindowIcon(app_icon)
    ui_scaling = UIScalingService()
    saved_mode = ui_scaling.load_mode()
    resolved_mode = ui_scaling.resolve_mode(
        cli=None,
        env=os.getenv("KLIPPCONFIG_UI_SCALE"),
        saved=saved_mode,
    )
    ui_scaling.apply(app, resolved_mode)

    window = MainWindow(ui_scaling_service=ui_scaling, active_scale_mode=resolved_mode)
    if icon_file.exists():
        window.setWindowIcon(app.windowIcon())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from app.services.ui_scaling import DEFAULT_MODE, UIScalingService


def _settings_file(tmp_path, name: str = "ui-scaling.ini") -> QSettings:
    return QSettings(str(tmp_path / name), QSettings.Format.IniFormat)


def test_load_mode_defaults_to_auto_on_missing_or_invalid(tmp_path) -> None:
    settings = _settings_file(tmp_path)
    settings.clear()
    settings.sync()
    service = UIScalingService(settings=settings)

    assert service.load_mode() == DEFAULT_MODE

    settings.setValue("ui/scale_mode", "not-a-mode")
    settings.sync()
    assert service.load_mode() == DEFAULT_MODE


def test_save_mode_persists_value(tmp_path) -> None:
    settings = _settings_file(tmp_path)
    settings.clear()
    service = UIScalingService(settings=settings)

    service.save_mode("90")
    assert service.load_mode() == "90"


def test_resolve_mode_precedence(tmp_path) -> None:
    service = UIScalingService(settings=_settings_file(tmp_path, "resolve.ini"))

    assert service.resolve_mode(cli="85", env="125", saved="100") == "85"
    assert service.resolve_mode(cli=None, env="125", saved="100") == "125"
    assert service.resolve_mode(cli=None, env="invalid", saved="110") == "110"
    assert service.resolve_mode(cli=None, env=None, saved=None) == DEFAULT_MODE


def test_apply_scales_font_from_baseline(qapp, tmp_path) -> None:
    UIScalingService._baseline_point_sizes.clear()

    original_font = qapp.font()
    font = qapp.font()
    font.setPointSizeF(10.0)
    qapp.setFont(font)

    try:
        service = UIScalingService(settings=_settings_file(tmp_path, "apply.ini"))
        service.apply(qapp, "100")
        base_size = qapp.font().pointSizeF()
        assert base_size == pytest.approx(10.0, rel=0.01)

        service.apply(qapp, "90")
        assert qapp.font().pointSizeF() == pytest.approx(9.0, rel=0.01)

        service.apply(qapp, "125")
        assert qapp.font().pointSizeF() == pytest.approx(12.5, rel=0.01)
    finally:
        qapp.setFont(original_font)

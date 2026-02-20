from __future__ import annotations

from app.services.firmware_tools import FirmwareToolsService


def test_refactor_cfg_normalizes_sections_and_key_value_style() -> None:
    service = FirmwareToolsService()
    source = (
        "  [ printer ]  \n"
        "max_velocity = 250  \n"
        "\n"
        "\n"
        "[extruder]\n"
        "max_temp=280\n"
    )

    refactored, changes = service.refactor_cfg(source)

    assert changes > 0
    assert "[printer]" in refactored
    assert "max_velocity: 250" in refactored
    assert "max_temp: 280" in refactored
    assert "\n\n\n" not in refactored


def test_validate_cfg_reports_blocking_and_warnings() -> None:
    service = FirmwareToolsService()
    source = (
        "[printer]\n"
        "kinematics: unknown_mode\n"
        "max_velocity: nope\n"
        "\n"
        "[extruder]\n"
        "max_temp = 280\n"
    )

    report = service.validate_cfg(source, source_label="printer.cfg")
    codes = {finding.code for finding in report.findings}

    assert report.has_blocking
    assert report.has_warnings
    assert "CFG_NUMERIC_INVALID" in codes
    assert "CFG_KINEMATICS_UNKNOWN" in codes
    assert "CFG_EQUALS_STYLE" in codes


def test_validate_cfg_handles_empty_content() -> None:
    service = FirmwareToolsService()
    report = service.validate_cfg("", source_label="empty.cfg")
    assert report.has_blocking
    assert any(finding.code == "CFG_EMPTY" for finding in report.findings)

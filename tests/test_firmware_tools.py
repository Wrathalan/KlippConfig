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


def test_validate_cfg_accepts_numeric_values_with_inline_comments() -> None:
    service = FirmwareToolsService()
    source = (
        "[printer]\n"
        "kinematics: corexy\n"
        "max_velocity: 400\n"
        "max_accel: 5000             # max 4000\n"
        "max_z_velocity: 20          # max 15 for 12V\n"
        "max_z_accel: 1000\n"
        "square_corner_velocity: 5.0\n"
    )

    report = service.validate_cfg(source, source_label="printer.cfg")
    codes = {finding.code for finding in report.findings}
    assert "CFG_NUMERIC_INVALID" not in codes
    assert "CFG_NUMERIC_NON_POSITIVE" not in codes


def test_validate_cfg_detects_suspicious_concatenated_key_values() -> None:
    service = FirmwareToolsService()
    source = (
        "[stepper_z]\n"
        "endstop_pin: probe:z_virtual_endstopposition_max: 310\n"
    )
    report = service.validate_cfg(source, source_label="motion.cfg")
    codes = {finding.code for finding in report.findings}
    assert "CFG_POSSIBLE_CONCATENATED_KEY_VALUE" in codes


def test_validate_cfg_handles_empty_content() -> None:
    service = FirmwareToolsService()
    report = service.validate_cfg("", source_label="empty.cfg")
    assert report.has_blocking
    assert any(finding.code == "CFG_EMPTY" for finding in report.findings)


def test_validate_cfg_supports_include_trailing_comments_and_wildcards() -> None:
    service = FirmwareToolsService()
    source = (
        "[include ./KAMP/Adaptive_Meshing.cfg]  # comment\n"
        "[include AFC/*.cfg]\n"
        "[gcode_macro TEST]\n"
        "gcode:\n"
        "  RESPOND MSG=\"ok\"\n"
    )

    report = service.validate_cfg(
        source,
        source_label="fragment.cfg",
        role="include_fragment",
    )
    codes = {finding.code for finding in report.findings}

    assert "CFG_SECTION_SYNTAX" not in codes
    assert "CFG_INCLUDE_SUFFIX" not in codes
    assert "CFG_PRINTER_SECTION_MISSING" not in codes
    assert "CFG_COMMON_SECTION_MISSING" not in codes


def test_validate_graph_reports_missing_include_and_cycle() -> None:
    service = FirmwareToolsService()
    files = {
        "printer.cfg": "[printer]\nkinematics: corexy\n[include extras.cfg]\n[include missing.cfg]\n",
        "extras.cfg": "[include printer.cfg]\n[gcode_macro TEST]\ngcode:\n  RESPOND MSG=\"x\"\n",
    }

    report = service.validate_graph(files, "printer.cfg")
    codes = [finding.code for finding in report.findings]

    assert "CFG_INCLUDE_MISSING" in codes
    assert "CFG_INCLUDE_CYCLE" in codes

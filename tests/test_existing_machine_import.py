from __future__ import annotations

import zipfile
from pathlib import Path

from app.domain.models import ProjectConfig
from app.services.existing_machine_import import ExistingMachineImportService


def _fixture_root() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "existing_machine_sample"


def _base_project_payload() -> dict:
    return {
        "preset_id": "voron_2_4_300",
        "board": "btt_octopus_1_1",
        "dimensions": {"x": 300, "y": 300, "z": 300},
        "probe": {"enabled": False, "type": None},
        "thermistors": {
            "hotend": "EPCOS 100K B57560G104F",
            "bed": "EPCOS 100K B57560G104F",
        },
        "motion_profile": "safe",
        "macro_packs": [],
        "addons": [],
        "toolhead": {"enabled": False, "board": None, "canbus_uuid": None},
        "leds": {
            "enabled": False,
            "pin": "PA8",
            "chain_count": 1,
            "color_order": "GRB",
            "initial_red": 0.0,
            "initial_green": 0.0,
            "initial_blue": 0.0,
        },
        "advanced_overrides": {},
    }


def test_import_folder_detects_machine_traits_and_addons() -> None:
    service = ExistingMachineImportService()
    profile = service.import_folder(str(_fixture_root()))

    assert profile.root_file == "config/printer.cfg"
    assert profile.detected["preset_id"] == "voron_2_4_350"
    assert profile.detected["toolhead"]["board"] == "ldo_nitehawk_sb"
    addon_ids = {entry["id"] for entry in profile.detected["addons"]}
    assert {"box_turtle", "kamp", "stealthburner_leds", "timelapse"}.issubset(addon_ids)
    assert "config/AFC/AFC.cfg" in profile.include_graph["config/printer.cfg"]
    assert "machine_attributes" in profile.detected
    assert profile.detected["machine_attributes"]["mcu_map"]["mcu"]["serial"]
    assert "section_map" in profile.detected
    assert "config/printer.cfg" in profile.detected["section_map"]


def test_import_zip_matches_folder_core_signals(tmp_path) -> None:
    fixture_root = _fixture_root()
    zip_path = tmp_path / "existing_machine_sample.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in fixture_root.rglob("*"):
            if not file_path.is_file():
                continue
            archive.write(file_path, file_path.relative_to(fixture_root))

    service = ExistingMachineImportService()
    profile = service.import_zip(str(zip_path))

    assert profile.root_file == "config/printer.cfg"
    assert profile.detected["preset_id"] == "voron_2_4_350"
    assert any(s.field == "toolhead.board" and s.value == "ldo_nitehawk_sb" for s in profile.suggestions)


def test_apply_suggestions_updates_project_with_auto_apply_fields() -> None:
    service = ExistingMachineImportService()
    profile = service.import_folder(str(_fixture_root()))
    project = ProjectConfig.model_validate(_base_project_payload())

    updated = service.apply_suggestions(profile, project)

    assert updated.preset_id == "voron_2_4_350"
    assert updated.dimensions.x == 350
    assert updated.dimensions.y == 350
    assert updated.toolhead.enabled is True
    assert updated.toolhead.board == "ldo_nitehawk_sb"
    assert "box_turtle" in updated.addons
    assert "kamp" in updated.addons
    assert updated.schema_version == 2
    assert updated.output_layout == "source_tree"
    assert updated.machine_attributes.root_file == "config/printer.cfg"
    assert "config/printer.cfg" in updated.section_map


def test_import_detects_and_applies_thermistors(tmp_path) -> None:
    source = tmp_path / "machine"
    config_dir = source / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "printer.cfg").write_text(
        "[mcu]\n"
        "serial: /dev/serial/by-id/usb-Klipper_stm32f446xx_TEST-if00\n\n"
        "[printer]\n"
        "kinematics: corexy\n\n"
        "[stepper_x]\n"
        "position_max: 350\n\n"
        "[stepper_y]\n"
        "position_max: 350\n\n"
        "[stepper_z]\n"
        "position_max: 310\n\n"
        "[stepper_z1]\n"
        "step_pin: PG4\n\n"
        "[stepper_z2]\n"
        "step_pin: PF9\n\n"
        "[stepper_z3]\n"
        "step_pin: PC13\n\n"
        "[extruder]\n"
        "sensor_type: ATC Semitec 104NT-4-R025H42G\n\n"
        "[heater_bed]\n"
        "sensor_type: Generic 3950\n\n"
        "[quad_gantry_level]\n"
        "gantry_corners:\n"
        "  -60,-10\n"
        "  410,420\n",
        encoding="utf-8",
    )

    service = ExistingMachineImportService()
    profile = service.import_folder(str(source))

    assert profile.detected["thermistors"]["hotend"] == "ATC Semitec 104NT-4-R025H42G"
    assert profile.detected["thermistors"]["bed"] == "Generic 3950"
    assert any(
        s.field == "thermistors.hotend"
        and s.value == "ATC Semitec 104NT-4-R025H42G"
        and s.auto_apply
        for s in profile.suggestions
    )
    assert any(
        s.field == "thermistors.bed"
        and s.value == "Generic 3950"
        and s.auto_apply
        for s in profile.suggestions
    )

    project = ProjectConfig.model_validate(_base_project_payload())
    updated = service.apply_suggestions(profile, project)
    assert updated.thermistors.hotend == "ATC Semitec 104NT-4-R025H42G"
    assert updated.thermistors.bed == "Generic 3950"

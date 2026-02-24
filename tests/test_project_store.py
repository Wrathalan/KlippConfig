from __future__ import annotations

import json

from app.services.project_store import ProjectStoreService


def _v1_payload() -> dict:
    return {
        "preset_id": "voron_2_4_350",
        "board": "btt_octopus_1_1",
        "dimensions": {"x": 350, "y": 350, "z": 310},
        "probe": {"enabled": True, "type": "tap"},
        "thermistors": {
            "hotend": "ATC Semitec 104NT-4-R025H42G",
            "bed": "ATC Semitec 104NT-4-R025H42G",
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
        "advanced_overrides": {"motion.max_velocity": 400},
    }


def test_load_migrates_v1_payload_to_schema_v2(tmp_path) -> None:
    service = ProjectStoreService()
    path = tmp_path / "legacy_project.json"
    path.write_text(json.dumps(_v1_payload(), indent=2), encoding="utf-8")

    project = service.load(str(path))

    assert project.schema_version == 2
    assert project.output_layout == "source_tree"
    assert project.machine_attributes.printer_limits.max_velocity == 400
    assert isinstance(project.section_map, dict)
    assert isinstance(project.addon_configs.kamp.include_files, list)


def test_save_writes_schema_version_2(tmp_path) -> None:
    service = ProjectStoreService()
    path = tmp_path / "project_v2.json"
    # Build from migrated legacy payload to ensure save path writes v2.
    legacy_path = tmp_path / "legacy_input.json"
    legacy_path.write_text(json.dumps(_v1_payload(), indent=2), encoding="utf-8")
    project = service.load(str(legacy_path))

    service.save(str(path), project)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2
    assert saved["output_layout"] == "source_tree"


def test_load_strips_removed_afc_addon_from_v2_payload(tmp_path) -> None:
    service = ProjectStoreService()
    path = tmp_path / "project_with_afc.json"
    payload = _v1_payload()
    payload["schema_version"] = 2
    payload["output_layout"] = "source_tree"
    payload["addons"] = ["afc", "kamp"]
    payload["machine_attributes"] = {
        "root_file": "printer.cfg",
        "include_graph": {},
        "printer_limits": {
            "max_velocity": 300.0,
            "max_accel": 3000.0,
            "max_z_velocity": None,
            "max_z_accel": None,
            "square_corner_velocity": 5.0,
        },
        "mcu_map": {},
        "stepper_sections": {},
        "driver_sections": {},
        "probe_sections": {},
        "leveling_sections": {},
        "thermal_sections": {},
        "fan_sections": {},
        "sensor_sections": {},
        "resonance_sections": {},
    }
    payload["addon_configs"] = {
        "afc": {"enabled": True, "include_files": ["AFC/AFC.cfg"], "sections": {}},
        "kamp": {"enabled": False, "include_files": [], "sections": {}},
        "stealthburner_leds": {"enabled": False, "include_files": [], "sections": {}},
        "timelapse": {"enabled": False, "include_files": [], "sections": {}},
    }
    payload["section_map"] = {}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    project = service.load(str(path))

    assert "afc" not in project.addons
    assert "kamp" in project.addons
    assert hasattr(project.addon_configs, "kamp")

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
    assert {"afc", "box_turtle", "kamp", "stealthburner_leds", "timelapse"}.issubset(addon_ids)
    assert "config/AFC/AFC.cfg" in profile.include_graph["config/printer.cfg"]


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

from __future__ import annotations

import json

from app.domain.models import ProjectConfig
from app.services import board_registry
from app.services.config_bundles import BundleCatalogService
from app.services.preset_catalog import PresetCatalogService
from app.services.renderer import ConfigRenderService
from app.services.validator import ValidationService


def _write_json(path, payload: dict) -> None:  # noqa: ANN001
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _base_project_payload(preset_id: str, board: str, x: int, y: int, z: int) -> dict:
    return {
        "preset_id": preset_id,
        "board": board,
        "dimensions": {"x": x, "y": y, "z": z},
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


def test_bundle_catalog_loads_board_toolhead_and_addon_profiles(tmp_path) -> None:
    bundle_root = tmp_path / "bundles"
    _write_json(
        bundle_root / "boards" / "my_board.json",
        {
            "id": "my_board",
            "label": "My Board",
            "mcu": "stm32f446xx",
            "serial_hint": "/dev/serial/by-id/usb-My_Board",
            "pins": {"stepper_x_step": "PA0"},
            "layout": {"Drivers": ["X"]},
        },
    )
    _write_json(
        bundle_root / "toolhead_boards" / "my_toolhead.json",
        {
            "id": "my_toolhead",
            "label": "My Toolhead",
            "mcu": "rp2040",
            "serial_hint": "canbus_uuid: replace-with-uuid",
            "pins": {"extruder_step": "toolhead:EXT_STEP"},
        },
    )
    _write_json(
        bundle_root / "addons" / "my_addon.json",
        {
            "id": "my_addon",
            "label": "My Addon",
            "template": "addons/my_addon.cfg.j2",
            "supported_families": ["voron"],
        },
    )

    service = BundleCatalogService(bundle_roots=[bundle_root])

    main_boards = service.load_main_board_profiles()
    toolhead_boards = service.load_toolhead_board_profiles()
    addons = service.load_addon_profiles()

    assert "my_board" in main_boards
    assert main_boards["my_board"].label == "My Board"
    assert "my_toolhead" in toolhead_boards
    assert toolhead_boards["my_toolhead"].mcu == "rp2040"
    assert "my_addon" in addons
    assert addons["my_addon"].template == "addons/my_addon.cfg.j2"


def test_invalid_bundle_files_are_ignored(tmp_path) -> None:
    bundle_root = tmp_path / "bundles"
    invalid_board = bundle_root / "boards" / "broken.json"
    invalid_board.parent.mkdir(parents=True, exist_ok=True)
    invalid_board.write_text("{not-valid-json", encoding="utf-8")

    invalid_addon = bundle_root / "addons" / "missing_fields.json"
    _write_json(invalid_addon, {"id": "missing_fields"})

    service = BundleCatalogService(bundle_roots=[bundle_root])
    assert service.load_main_board_profiles() == {}
    assert service.load_addon_profiles() == {}


def test_custom_addon_bundle_renders_using_bundle_template(monkeypatch, tmp_path) -> None:
    bundle_root = tmp_path / "bundles"
    _write_json(
        bundle_root / "addons" / "chamber_heater.json",
        {
            "id": "chamber_heater",
            "label": "Chamber Heater",
            "template": "addons/chamber_heater.cfg.j2",
            "supported_families": ["voron"],
        },
    )
    template_path = bundle_root / "templates" / "addons" / "chamber_heater.cfg.j2"
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text(
        "[gcode_macro CHAMBER_HEATER_BUNDLE]\n"
        "gcode:\n"
        "  RESPOND TYPE=echo MSG=\"bundle-addon\"\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("KLIPPCONFIG_BUNDLE_DIRS", str(bundle_root))
    monkeypatch.setattr(board_registry, "_bundle_catalog", BundleCatalogService([bundle_root]))

    catalog = PresetCatalogService()
    validator = ValidationService()
    renderer = ConfigRenderService()
    preset = catalog.load_preset("voron_2_4_300")

    payload = _base_project_payload(
        preset.id,
        preset.supported_boards[0],
        preset.build_volume.x,
        preset.build_volume.y,
        preset.build_volume.z,
    )
    payload["addons"] = ["chamber_heater"]
    project = ProjectConfig.model_validate(payload)

    report = validator.validate_project(project, preset)
    assert not report.has_blocking

    pack = renderer.render(project, preset)
    assert "addons.cfg" in pack.files
    assert "CHAMBER_HEATER_BUNDLE" in pack.files["addons.cfg"]


def test_usb_toolhead_bundle_renders_serial_and_skips_can_uuid_requirement(
    monkeypatch, tmp_path
) -> None:
    bundle_root = tmp_path / "bundles"
    _write_json(
        bundle_root / "toolhead_boards" / "usb_toolhead.json",
        {
            "id": "usb_toolhead",
            "label": "USB Toolhead",
            "mcu": "rp2040",
            "transport": "usb",
            "serial_hint": "/dev/serial/by-id/usb-USB_Toolhead",
            "pins": {"extruder_step": "toolhead:EXT_STEP"},
        },
    )

    monkeypatch.setenv("KLIPPCONFIG_BUNDLE_DIRS", str(bundle_root))
    monkeypatch.setattr(board_registry, "_bundle_catalog", BundleCatalogService([bundle_root]))

    catalog = PresetCatalogService()
    validator = ValidationService()
    renderer = ConfigRenderService()
    preset = catalog.load_preset("voron_2_4_300")

    payload = _base_project_payload(
        preset.id,
        preset.supported_boards[0],
        preset.build_volume.x,
        preset.build_volume.y,
        preset.build_volume.z,
    )
    payload["toolhead"] = {
        "enabled": True,
        "board": "usb_toolhead",
        "canbus_uuid": None,
    }
    project = ProjectConfig.model_validate(payload)

    assert board_registry.toolhead_board_transport("usb_toolhead") == "usb"
    assert "usb_toolhead" in board_registry.list_usb_toolhead_boards()
    assert "usb_toolhead" not in board_registry.list_can_toolhead_boards()

    report = validator.validate_project(project, preset)
    assert not report.has_blocking

    pack = renderer.render(project, preset)
    assert "toolhead.cfg" in pack.files
    assert "serial: /dev/serial/by-id/usb-USB_Toolhead" in pack.files["toolhead.cfg"]
    assert "canbus_uuid:" not in pack.files["toolhead.cfg"]

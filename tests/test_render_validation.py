from app.domain.models import ProjectConfig
from app.services.preset_catalog import PresetCatalogService
from app.services.renderer import ConfigRenderService
from app.services.validator import ValidationService


def _base_project(preset_id: str, board: str, x: int, y: int, z: int) -> dict:
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


def test_each_preset_renders_required_files() -> None:
    catalog = PresetCatalogService()
    renderer = ConfigRenderService()
    validator = ValidationService()

    required_files = {
        "printer.cfg",
        "mcu.cfg",
        "board_pins.cfg",
        "motion.cfg",
        "thermal.cfg",
        "input_shaper.cfg",
        "BOARD-LAYOUT.md",
        "README-next-steps.md",
        "CALIBRATION-CHECKLIST.md",
    }

    for summary in catalog.list_presets():
        preset = catalog.load_preset(summary.id)
        payload = _base_project(
            preset.id,
            preset.supported_boards[0],
            preset.build_volume.x,
            preset.build_volume.y,
            preset.build_volume.z,
        )
        if not preset.feature_flags.probe_optional:
            payload["probe"] = {
                "enabled": True,
                "type": (preset.recommended_probe_types[0] if preset.recommended_probe_types else "tap"),
            }

        project = ProjectConfig.model_validate(payload)
        project_report = validator.validate_project(project, preset)
        assert not project_report.has_blocking

        pack = renderer.render(project, preset)
        rendered_report = validator.validate_rendered(pack)
        assert not rendered_report.has_blocking
        assert required_files.issubset(set(pack.files.keys()))


def test_toolhead_and_addons_render_expected_files() -> None:
    catalog = PresetCatalogService()
    renderer = ConfigRenderService()
    validator = ValidationService()

    preset = catalog.load_preset("voron_2_4_300")
    project = ProjectConfig.model_validate(
        {
            **_base_project(
                preset.id,
                preset.supported_boards[0],
                preset.build_volume.x,
                preset.build_volume.y,
                preset.build_volume.z,
            ),
            "toolhead": {
                "enabled": True,
                "board": preset.supported_toolhead_boards[0],
                "canbus_uuid": "abcdef1234567890",
            },
            "addons": ["filament_buffer"],
            "macro_packs": ["core_maintenance"],
        }
    )

    project_report = validator.validate_project(project, preset)
    assert not project_report.has_blocking

    pack = renderer.render(project, preset)
    assert "toolhead.cfg" in pack.files
    assert "toolhead_pins.cfg" in pack.files
    assert "addons.cfg" in pack.files
    assert "macros.cfg" in pack.files

    printer_cfg = pack.files["printer.cfg"]
    assert "[include toolhead.cfg]" in printer_cfg
    assert "[include toolhead_pins.cfg]" in printer_cfg
    assert "[include addons.cfg]" in printer_cfg
    assert "[include macros.cfg]" in printer_cfg


def test_led_control_generates_leds_cfg_and_include() -> None:
    catalog = PresetCatalogService()
    renderer = ConfigRenderService()
    validator = ValidationService()

    preset = catalog.load_preset("voron_2_4_300")
    project = ProjectConfig.model_validate(
        {
            **_base_project(
                preset.id,
                preset.supported_boards[0],
                preset.build_volume.x,
                preset.build_volume.y,
                preset.build_volume.z,
            ),
            "leds": {
                "enabled": True,
                "pin": "PA8",
                "chain_count": 8,
                "color_order": "GRB",
                "initial_red": 0.0,
                "initial_green": 0.0,
                "initial_blue": 0.05,
            },
        }
    )

    project_report = validator.validate_project(project, preset)
    assert not project_report.has_blocking

    pack = renderer.render(project, preset)
    assert "leds.cfg" in pack.files
    assert "[include leds.cfg]" in pack.files["printer.cfg"]

    rendered_report = validator.validate_rendered(pack)
    assert not rendered_report.has_blocking


def test_multi_material_addon_conflict_blocks_validation() -> None:
    catalog = PresetCatalogService()
    validator = ValidationService()
    preset = catalog.load_preset("voron_2_4_350")

    project = ProjectConfig.model_validate(
        {
            **_base_project(
                preset.id,
                preset.supported_boards[0],
                preset.build_volume.x,
                preset.build_volume.y,
                preset.build_volume.z,
            ),
            "addons": ["ams_lite", "ercf_v2"],
        }
    )
    report = validator.validate_project(project, preset)
    assert report.has_blocking
    assert any(f.code == "MULTI_MATERIAL_ADDON_CONFLICT" for f in report.findings)


def test_qgl_requires_probe() -> None:
    catalog = PresetCatalogService()
    validator = ValidationService()
    preset = catalog.load_preset("voron_2_4_300")

    project = ProjectConfig.model_validate(
        {
            **_base_project(
                preset.id,
                preset.supported_boards[0],
                preset.build_volume.x,
                preset.build_volume.y,
                preset.build_volume.z,
            ),
            "probe": {"enabled": False, "type": None},
            "macro_packs": ["qgl_helpers"],
        }
    )
    report = validator.validate_project(project, preset)
    assert report.has_blocking
    assert any(f.code == "QGL_REQUIRES_PROBE" for f in report.findings)


def test_ldo_toolhead_boards_are_available_for_voron_presets() -> None:
    catalog = PresetCatalogService()
    validator = ValidationService()

    ldo_board_ids = {"ldo_nitehawk_sb", "ldo_nitehawk_36"}
    for summary in catalog.list_presets():
        preset = catalog.load_preset(summary.id)
        assert ldo_board_ids.issubset(set(preset.supported_toolhead_boards))

    preset = catalog.load_preset("voron_2_4_300")
    project = ProjectConfig.model_validate(
        {
            **_base_project(
                preset.id,
                preset.supported_boards[0],
                preset.build_volume.x,
                preset.build_volume.y,
                preset.build_volume.z,
            ),
            "toolhead": {
                "enabled": True,
                "board": "ldo_nitehawk_sb",
                "canbus_uuid": "abcdef1234567890",
            },
        }
    )
    report = validator.validate_project(project, preset)
    assert not report.has_blocking


def test_led_enabled_requires_pin() -> None:
    catalog = PresetCatalogService()
    validator = ValidationService()
    preset = catalog.load_preset("voron_2_4_300")

    project = ProjectConfig.model_validate(
        {
            **_base_project(
                preset.id,
                preset.supported_boards[0],
                preset.build_volume.x,
                preset.build_volume.y,
                preset.build_volume.z,
            ),
            "leds": {
                "enabled": True,
                "pin": "",
                "chain_count": 1,
                "color_order": "GRB",
                "initial_red": 0.0,
                "initial_green": 0.0,
                "initial_blue": 0.0,
            },
        }
    )
    report = validator.validate_project(project, preset)
    assert report.has_blocking
    assert any(f.code == "LED_PIN_REQUIRED" for f in report.findings)

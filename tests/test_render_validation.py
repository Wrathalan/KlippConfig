from collections import OrderedDict

from app.domain.models import ProjectConfig
from app.domain.models import RenderedPack
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


def test_toolhead_and_disabled_addons_render_expected_files() -> None:
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
    assert any(f.code == "ADDONS_DISABLED" for f in project_report.findings)

    pack = renderer.render(project, preset)
    assert "toolhead.cfg" in pack.files
    assert "toolhead_pins.cfg" in pack.files
    assert "addons.cfg" not in pack.files
    assert "macros.cfg" in pack.files

    printer_cfg = pack.files["printer.cfg"]
    assert "[include toolhead.cfg]" in printer_cfg
    assert "[include toolhead_pins.cfg]" in printer_cfg
    assert "[include addons.cfg]" not in printer_cfg
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


def test_addons_are_reported_as_disabled_warning() -> None:
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
    assert not report.has_blocking
    assert any(f.code == "ADDONS_DISABLED" for f in report.findings)


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


def test_validate_rendered_flags_cfg_syntax_defects_as_blocking() -> None:
    validator = ValidationService()
    pack = RenderedPack(
        files=OrderedDict(
            {
                "printer.cfg": (
                    "[include mcu.cfg]\n"
                    "[include board_pins.cfg]\n"
                    "[include motion.cfg]\n"
                    "[include thermal.cfg]\n"
                    "[include input_shaper.cfg]\n\n"
                    "[printer]\n"
                    "kinematics: corexy\n"
                    "max_velocity: 300\n"
                    "max_accel: 3000\n"
                    "square_corner_velocity: 5.0\n"
                ),
                "mcu.cfg": "[mcu]\nserial: /dev/serial/by-id/test\n",
                "board_pins.cfg": "[board_pins mainboard]\naliases:\n  stepper_x_step=PF13\n",
                "motion.cfg": (
                    "[stepper_z]\n"
                    "endstop_pin: probe:z_virtual_endstopposition_max: 310\n"
                ),
                "thermal.cfg": "[extruder]\nheater_pin: PA2\nsensor_pin: PF4\nsensor_type: Generic 3950\n",
                "input_shaper.cfg": "[input_shaper]\nshaper_type_x: mzv\nshaper_freq_x: 45.0\n",
                "BOARD-LAYOUT.md": "layout\n",
                "README-next-steps.md": "next\n",
                "CALIBRATION-CHECKLIST.md": "checklist\n",
            }
        )
    )

    report = validator.validate_rendered(pack)
    assert report.has_blocking
    assert any(f.code == "RENDER_CFG_SYNTAX" for f in report.findings)


def test_source_tree_layout_renders_from_section_map() -> None:
    catalog = PresetCatalogService()
    renderer = ConfigRenderService()
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
            "output_layout": "source_tree",
            "machine_attributes": {
                "root_file": "config/printer.cfg",
                "include_graph": {"config/printer.cfg": ["config/nhk.cfg"], "config/nhk.cfg": []},
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
            },
            "section_map": {
                "config/printer.cfg": {
                    "include nhk.cfg": {},
                    "printer": {"kinematics": "corexy", "max_velocity": "300", "max_accel": "3000", "square_corner_velocity": "5.0"},
                },
                "config/nhk.cfg": {
                    "mcu nhk": {"serial": "/dev/serial/by-id/usb-Klipper_rp2040_TEST-if00"}
                },
            },
        }
    )

    pack = renderer.render(project, preset)
    assert pack.metadata.get("layout") == "source_tree"
    assert "config/printer.cfg" in pack.files
    assert "config/nhk.cfg" in pack.files
    assert "[include nhk.cfg]" in pack.files["config/printer.cfg"]

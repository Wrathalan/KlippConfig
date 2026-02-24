from __future__ import annotations

from app.services.klipper_ast import parse_klipper_config, render_klipper_config


def test_parser_handles_sections_includes_and_multiline_values() -> None:
    source = (
        "# header\n"
        "[include ./KAMP/Adaptive_Meshing.cfg]\n"
        "[gcode_macro PRINT_START]\n"
        "gcode:\n"
        "  G28\n"
        "  BED_MESH_CALIBRATE\n"
        "[printer]\n"
        "kinematics: corexy\n"
    )
    document = parse_klipper_config(source)
    assert document.section_names() == [
        "include ./KAMP/Adaptive_Meshing.cfg",
        "gcode_macro PRINT_START",
        "printer",
    ]

    macro_section = document.sections[1]
    assert macro_section.name == "gcode_macro PRINT_START"
    assert macro_section.entries[0].key_value is not None
    assert macro_section.entries[0].key_value.key == "gcode"
    assert macro_section.entries[0].key_value.continuations == ["  G28", "  BED_MESH_CALIBRATE"]

    include_section = document.sections[0]
    assert include_section.is_include_section is True
    assert include_section.include_target == "./KAMP/Adaptive_Meshing.cfg"


def test_render_roundtrips_semantic_content() -> None:
    source = (
        "; preamble comment\n"
        "\n"
        "[printer]\n"
        "kinematics: corexy\n"
        "max_velocity = 300\n"
        "\n"
        "[gcode_macro TEST]\n"
        "gcode:\n"
        "  RESPOND MSG=\"ok\"\n"
    )

    parsed = parse_klipper_config(source)
    rendered = render_klipper_config(parsed)
    reparsed = parse_klipper_config(rendered)

    assert reparsed.section_names() == parsed.section_names()
    assert reparsed.to_section_key_map() == parsed.to_section_key_map()

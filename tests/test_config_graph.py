from __future__ import annotations

from app.services.config_graph import ConfigGraphService


def test_resolve_includes_accepts_trailing_comments_and_wildcards() -> None:
    service = ConfigGraphService()
    content = (
        "[include ./KAMP/Adaptive_Meshing.cfg]  # trailing comment\n"
        "[include AFC/*.cfg]\n"
    )

    includes = service.resolve_includes("config/KAMP_Settings.cfg", content)

    assert includes == [
        "config/KAMP/Adaptive_Meshing.cfg",
        "config/AFC/*.cfg",
    ]


def test_build_graph_and_flatten_expand_nested_includes() -> None:
    service = ConfigGraphService()
    files = {
        "config/printer.cfg": (
            "[include AFC/*.cfg]\n"
            "[include KAMP_Settings.cfg]\n"
        ),
        "config/AFC/AFC.cfg": "[include mcu/AFC_Lite.cfg]\n",
        "config/AFC/mcu/AFC_Lite.cfg": "[mcu afc]\nserial: test\n",
        "config/KAMP_Settings.cfg": "[include ./KAMP/Adaptive_Meshing.cfg]\n",
        "config/KAMP/Adaptive_Meshing.cfg": "[gcode_macro ADAPTIVE]\n",
    }

    graph = service.build_graph(files, "config/printer.cfg")
    order = service.flatten_graph(graph, "config/printer.cfg")

    assert graph["config/printer.cfg"] == [
        "config/AFC/AFC.cfg",
        "config/KAMP_Settings.cfg",
    ]
    assert graph["config/AFC/AFC.cfg"] == ["config/AFC/mcu/AFC_Lite.cfg"]
    assert graph["config/KAMP_Settings.cfg"] == ["config/KAMP/Adaptive_Meshing.cfg"]
    assert order[0] == "config/printer.cfg"
    assert "config/AFC/mcu/AFC_Lite.cfg" in order
    assert "config/KAMP/Adaptive_Meshing.cfg" in order

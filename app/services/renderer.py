from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.domain.models import Preset, ProjectConfig, RenderedPack
from app.services.board_registry import (
    get_addon_profile,
    get_board_profile,
    get_toolhead_board_profile,
    toolhead_board_transport,
)
from app.services.paths import bundle_template_dirs, templates_dir as default_templates_dir
from app.services.config_graph import ConfigGraphService


FALLBACK_PINS = {
    "stepper_x_step": "PA0",
    "stepper_x_dir": "PA1",
    "stepper_x_enable": "PA2",
    "stepper_y_step": "PA3",
    "stepper_y_dir": "PA4",
    "stepper_y_enable": "PA5",
    "stepper_z_step": "PA6",
    "stepper_z_dir": "PA7",
    "stepper_z_enable": "PA8",
    "extruder_step": "PB0",
    "extruder_dir": "PB1",
    "extruder_enable": "PB2",
    "heater_bed": "PB10",
    "heater_hotend": "PB11",
    "temp_bed": "PC0",
    "temp_hotend": "PC1",
    "probe": "PC2",
    "toolhead_fan": "PC6",
    "part_cooling_fan": "PC7",
    "filament_sensor": "PC8",
    "ams_step": "PC9",
    "ams_dir": "PC10",
    "ams_enable": "PC11",
}


class ConfigRenderService:
    def __init__(self, template_root: Path | None = None) -> None:
        self.template_root = template_root or default_templates_dir()
        loader_paths = [str(self.template_root)]
        for candidate in bundle_template_dirs():
            if candidate.exists() and candidate.is_dir():
                loader_paths.append(str(candidate))
        self.env = Environment(
            loader=FileSystemLoader(loader_paths),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            undefined=StrictUndefined,
        )
        self.graph_service = ConfigGraphService()

    @staticmethod
    def _coerce_override(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if default is None:
            return value
        try:
            if isinstance(default, bool):
                if isinstance(value, str):
                    lowered = value.strip().lower()
                    if lowered in {"1", "true", "yes", "on"}:
                        return True
                    if lowered in {"0", "false", "no", "off"}:
                        return False
                return bool(value)
            if isinstance(default, int):
                return int(value)
            if isinstance(default, float):
                return float(value)
        except (ValueError, TypeError):
            return default
        return value

    def _compose_context(self, project: ProjectConfig, preset: Preset) -> dict[str, Any]:
        board_profile = preset.board_profiles.get(project.board) or get_board_profile(project.board)
        toolhead_profile = (
            get_toolhead_board_profile(project.toolhead.board)
            if project.toolhead.enabled and project.toolhead.board
            else None
        )
        toolhead_transport = (
            toolhead_board_transport(project.toolhead.board)
            if project.toolhead.enabled and project.toolhead.board
            else None
        )
        pins = dict(FALLBACK_PINS)
        if board_profile:
            pins.update(board_profile.pins)
        if toolhead_profile:
            pins.update(toolhead_profile.pins)

        for key, value in project.advanced_overrides.items():
            if key.startswith("pins."):
                pin_key = key.split(".", 1)[1]
                pins[pin_key] = str(value)

        def override(name: str, default: Any) -> Any:
            if name not in project.advanced_overrides:
                return default
            return self._coerce_override(project.advanced_overrides[name], default)

        return {
            "project": project,
            "preset": preset,
            "board_profile": board_profile,
            "toolhead_profile": toolhead_profile,
            "toolhead_transport": toolhead_transport,
            "pins": pins,
            "motion": {
                "max_velocity": override("motion.max_velocity", preset.defaults.max_velocity),
                "max_accel": override("motion.max_accel", preset.defaults.max_accel),
                "square_corner_velocity": override(
                    "motion.square_corner_velocity", preset.defaults.square_corner_velocity
                ),
            },
            "override": override,
        }

    def _render_template(self, template_name: str, context: dict[str, Any]) -> str:
        template = self.env.get_template(template_name)
        return template.render(**context).strip() + "\n"

    def _render_macro_pack(self, pack_name: str, context: dict[str, Any]) -> str:
        template_name = f"macros/{pack_name}.cfg.j2"
        return self._render_template(template_name, context)

    def _render_addon(self, addon_name: str, context: dict[str, Any]) -> str:
        addon_profile = get_addon_profile(addon_name)
        template_name = (
            addon_profile.template if addon_profile else f"addons/{addon_name}.cfg.j2"
        )
        return self._render_template(template_name, context)

    @staticmethod
    def _render_section_map_file(sections: dict[str, dict[str, str]]) -> str:
        lines: list[str] = []
        for section_name, values in sections.items():
            lines.append(f"[{section_name}]")
            if isinstance(values, dict):
                for key, value in values.items():
                    rendered = str(value) if value is not None else ""
                    if "\n" in rendered:
                        value_lines = rendered.splitlines()
                        first_line = value_lines[0] if value_lines else ""
                        if first_line:
                            lines.append(f"{key}: {first_line}")
                        else:
                            lines.append(f"{key}:")
                        lines.extend(value_lines[1:])
                    else:
                        if rendered:
                            lines.append(f"{key}: {rendered}")
                        else:
                            lines.append(f"{key}:")
            lines.append("")

        if not lines:
            return ""
        return "\n".join(lines).rstrip() + "\n"

    def _render_modular(self, project: ProjectConfig, preset: Preset) -> RenderedPack:
        context = self._compose_context(project, preset)
        files: OrderedDict[str, str] = OrderedDict()
        files["printer.cfg"] = self._render_template(preset.templates.printer, context)
        files["mcu.cfg"] = self._render_template(preset.templates.mcu, context)
        files["board_pins.cfg"] = self._render_template("board_pins.cfg.j2", context)
        if project.toolhead.enabled and context["toolhead_profile"]:
            files["toolhead.cfg"] = self._render_template("toolhead.cfg.j2", context)
            files["toolhead_pins.cfg"] = self._render_template("toolhead_pins.cfg.j2", context)
        files["motion.cfg"] = self._render_template(preset.templates.motion, context)
        files["thermal.cfg"] = self._render_template(preset.templates.thermal, context)
        files["input_shaper.cfg"] = self._render_template("input_shaper.cfg.j2", context)
        if project.leds.enabled:
            files["leds.cfg"] = self._render_template("leds.cfg.j2", context)

        if project.addons:
            addon_sections: list[str] = [self._render_template("addons.cfg.j2", context).strip()]
            for addon_name in sorted(project.addons):
                addon_sections.append(self._render_addon(addon_name, context).strip())
            files["addons.cfg"] = "\n\n".join(addon_sections).strip() + "\n"

        if project.macro_packs and preset.feature_flags.macros_supported:
            macro_sections: list[str] = []
            for pack_name in sorted(project.macro_packs):
                macro_sections.append(self._render_macro_pack(pack_name, context).strip())
            files["macros.cfg"] = "\n\n".join(macro_sections).strip() + "\n"

        files["BOARD-LAYOUT.md"] = self._render_template("BOARD-LAYOUT.md.j2", context)
        files["README-next-steps.md"] = self._render_template("README-next-steps.md.j2", context)
        files["CALIBRATION-CHECKLIST.md"] = self._render_template(
            "CALIBRATION-CHECKLIST.md.j2", context
        )
        return RenderedPack(
            files=files,
            metadata={
                "preset_id": preset.id,
                "preset_name": preset.name,
                "board": project.board,
                "toolhead_board": project.toolhead.board,
                "leds_enabled": project.leds.enabled,
                "addons": list(project.addons),
                "layout": "modular",
            },
        )

    def _render_source_tree(self, project: ProjectConfig, preset: Preset) -> RenderedPack:
        context = self._compose_context(project, preset)
        section_map = project.section_map if isinstance(project.section_map, dict) else {}
        files: OrderedDict[str, str] = OrderedDict()
        if not section_map:
            modular = self._render_modular(project, preset)
            for addon_name in sorted(project.addons):
                addon_profile = get_addon_profile(addon_name)
                if addon_profile is None or not addon_profile.package_templates:
                    continue
                printer_cfg = modular.files.get("printer.cfg", "")
                for include_file in addon_profile.include_files:
                    include_line = f"[include {include_file}]"
                    if include_line not in printer_cfg:
                        printer_cfg = printer_cfg.rstrip() + "\n" + include_line + "\n"
                modular.files["printer.cfg"] = printer_cfg
                for output_path, template_name in addon_profile.package_templates.items():
                    modular.files[output_path] = self._render_template(template_name, context)
            modular.metadata["layout"] = "source_tree"
            modular.metadata["source_root_file"] = "printer.cfg"
            return modular

        include_graph = project.machine_attributes.include_graph
        root_file = (project.machine_attributes.root_file or "printer.cfg").replace("\\", "/")
        ordered_candidates: list[str] = []
        if include_graph:
            ordered_candidates.extend(self.graph_service.flatten_graph(include_graph, root_file))

        ordered_paths: list[str] = []
        seen: set[str] = set()
        for candidate in ordered_candidates:
            if candidate in section_map and candidate not in seen:
                ordered_paths.append(candidate)
                seen.add(candidate)
        for candidate in section_map.keys():
            if candidate not in seen:
                ordered_paths.append(candidate)
                seen.add(candidate)

        for file_path in ordered_paths:
            sections = section_map.get(file_path)
            if not isinstance(sections, dict):
                continue
            files[file_path] = self._render_section_map_file(sections)

        # Preserve existing operator docs in source-tree exports.
        files["BOARD-LAYOUT.md"] = self._render_template("BOARD-LAYOUT.md.j2", context)
        files["README-next-steps.md"] = self._render_template("README-next-steps.md.j2", context)
        files["CALIBRATION-CHECKLIST.md"] = self._render_template(
            "CALIBRATION-CHECKLIST.md.j2", context
        )

        return RenderedPack(
            files=files,
            metadata={
                "preset_id": preset.id,
                "preset_name": preset.name,
                "board": project.board,
                "toolhead_board": project.toolhead.board,
                "leds_enabled": project.leds.enabled,
                "addons": list(project.addons),
                "layout": "source_tree",
                "source_root_file": root_file,
            },
        )

    def render(
        self,
        project: ProjectConfig,
        preset: Preset,
        layout: str | None = None,
    ) -> RenderedPack:
        selected_layout = (layout or getattr(project, "output_layout", "source_tree")).strip().lower()
        if selected_layout == "modular":
            return self._render_modular(project, preset)
        return self._render_source_tree(project, preset)

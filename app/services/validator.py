from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app.domain.models import Preset, ProjectConfig, RenderedPack, ValidationReport
from app.services.board_registry import (
    get_board_profile,
    get_toolhead_board_profile,
    toolhead_board_transport,
)
from app.services.paths import schemas_dir as default_schemas_dir
from app.services.preset_catalog import PresetCatalogService
from app.services.firmware_tools import FirmwareToolsService


class ValidationService:
    def __init__(self, schema_root: Path | None = None) -> None:
        schema_root = schema_root or default_schemas_dir()
        catalog = PresetCatalogService(schema_root=schema_root)
        self.project_schema_validator = Draft202012Validator(catalog.get_project_schema())
        self.firmware_tools_service = FirmwareToolsService()

    @staticmethod
    def _as_reportable_path(path_parts: list[Any]) -> str:
        if not path_parts:
            return ""
        return ".".join(str(part) for part in path_parts)

    def validate_project(self, project: ProjectConfig, preset: Preset) -> ValidationReport:
        report = ValidationReport()
        project_data = project.model_dump(mode="json")
        for err in self.project_schema_validator.iter_errors(project_data):
            report.add(
                severity="blocking",
                code="PROJECT_SCHEMA",
                message=err.message,
                field=self._as_reportable_path(list(err.absolute_path)),
            )

        board_profile = preset.board_profiles.get(project.board) or get_board_profile(project.board)
        if project.board not in preset.supported_boards:
            if board_profile is None:
                report.add(
                    severity="blocking",
                    code="BOARD_UNKNOWN",
                    message=f"Board '{project.board}' is unknown.",
                    field="board",
                )
            else:
                report.add(
                    severity="warning",
                    code="BOARD_NOT_CURATED",
                    message=(
                        f"Board '{project.board}' is not in preset '{preset.name}' curated list. "
                        "Validate pins and motor/heater mapping before deploy."
                    ),
                    field="board",
                )

        if project.addons:
            report.add(
                severity="warning",
                code="ADDONS_DISABLED",
                message=(
                    "Add-ons are currently disabled in this build and will be ignored "
                    "during validation and rendering."
                ),
                field="addons",
            )

        if project.toolhead.enabled:
            if not project.toolhead.board:
                report.add(
                    severity="blocking",
                    code="TOOLHEAD_BOARD_REQUIRED",
                    message="Toolhead board is enabled but no board is selected.",
                    field="toolhead.board",
                )
            profile = (
                get_toolhead_board_profile(project.toolhead.board)
                if project.toolhead.board
                else None
            )
            if project.toolhead.board and profile is None:
                report.add(
                    severity="blocking",
                    code="TOOLHEAD_BOARD_UNKNOWN",
                    message=f"Unknown toolhead board '{project.toolhead.board}'.",
                    field="toolhead.board",
                )
            elif project.toolhead.board not in preset.supported_toolhead_boards:
                report.add(
                    severity="warning",
                    code="TOOLHEAD_BOARD_NOT_CURATED",
                    message=(
                        f"Toolhead board '{project.toolhead.board}' is not in preset "
                        f"'{preset.name}' curated list. Verify pinout and CAN mapping."
                    ),
                    field="toolhead.board",
                )

            if profile is not None and project.toolhead.board:
                transport = toolhead_board_transport(project.toolhead.board)
                if transport == "can":
                    if not project.toolhead.canbus_uuid:
                        report.add(
                            severity="blocking",
                            code="TOOLHEAD_CANBUS_UUID_REQUIRED",
                            message="CAN toolhead board is enabled but canbus_uuid is empty.",
                            field="toolhead.canbus_uuid",
                        )
                elif project.toolhead.canbus_uuid:
                    report.add(
                        severity="warning",
                        code="TOOLHEAD_CANBUS_UUID_IGNORED",
                        message="USB toolhead board selected; canbus_uuid value will be ignored.",
                        field="toolhead.canbus_uuid",
                    )
        elif project.toolhead.board or project.toolhead.canbus_uuid:
            report.add(
                severity="warning",
                code="TOOLHEAD_SETTINGS_IGNORED",
                message="Toolhead board is disabled; toolhead board/UUID values will be ignored.",
                field="toolhead",
            )

        for axis in ("x", "y", "z"):
            requested = getattr(project.dimensions, axis)
            limit = getattr(preset.build_volume, axis)
            if requested > limit:
                report.add(
                    severity="blocking",
                    code="DIMENSION_EXCEEDS_PRESET",
                    message=f"Requested {axis.upper()} size {requested} exceeds preset limit {limit}.",
                    field=f"dimensions.{axis}",
                )

        if preset.kinematics == "corexy" and project.dimensions.x != project.dimensions.y:
            report.add(
                severity="warning",
                code="COREXY_NON_SQUARE",
                message="CoreXY presets are typically tuned for square XY beds. Verify belt path/tuning.",
                field="dimensions",
            )

        if not project.probe.enabled and "qgl_helpers" in project.macro_packs:
            report.add(
                severity="blocking",
                code="QGL_REQUIRES_PROBE",
                message="Macro pack 'qgl_helpers' requires an enabled probe.",
                field="macro_packs",
            )

        if project.probe.enabled and not project.probe.type:
            report.add(
                severity="blocking",
                code="PROBE_TYPE_REQUIRED",
                message="Probe is enabled but probe.type is empty.",
                field="probe.type",
            )

        if project.probe.enabled and preset.recommended_probe_types:
            if project.probe.type and project.probe.type not in preset.recommended_probe_types:
                recommended = ", ".join(preset.recommended_probe_types)
                report.add(
                    severity="warning",
                    code="PROBE_TYPE_UNUSUAL",
                    message=(
                        f"Probe type '{project.probe.type}' is not typical for this preset. "
                        f"Recommended: {recommended}."
                    ),
                    field="probe.type",
                )

        if preset.feature_flags.probe_optional is False and not project.probe.enabled:
            report.add(
                severity="blocking",
                code="PROBE_REQUIRED",
                message="Selected preset requires a probe-enabled setup.",
                field="probe.enabled",
            )

        if project.leds.enabled and not (project.leds.pin or "").strip():
            report.add(
                severity="blocking",
                code="LED_PIN_REQUIRED",
                message="LED control is enabled but no LED pin is set.",
                field="leds.pin",
            )

        pins = {}
        if board_profile:
            pins.update(board_profile.pins)
        if project.toolhead.enabled and project.toolhead.board:
            toolhead_profile = get_toolhead_board_profile(project.toolhead.board)
            if toolhead_profile:
                pins.update(toolhead_profile.pins)
        for key, value in project.advanced_overrides.items():
            if key.startswith("pins."):
                pin_name = key.split(".", 1)[1]
                pins[pin_name] = str(value)

        duplicates = [pin for pin, count in Counter(pins.values()).items() if pin and count > 1]
        if duplicates:
            report.add(
                severity="blocking",
                code="PIN_CONFLICT",
                message=f"Detected duplicate pin assignments: {', '.join(sorted(duplicates))}.",
                field="advanced_overrides",
            )

        return report

    def validate_rendered(self, pack: RenderedPack) -> ValidationReport:
        report = ValidationReport()
        layout = str(pack.metadata.get("layout") or "modular").strip().lower()
        source_root_file = str(pack.metadata.get("source_root_file") or "printer.cfg").strip()

        required_files = [
            "BOARD-LAYOUT.md",
            "README-next-steps.md",
            "CALIBRATION-CHECKLIST.md",
        ]
        if layout == "source_tree":
            required_files.append(source_root_file or "printer.cfg")
        else:
            required_files.extend(
                [
                    "printer.cfg",
                    "mcu.cfg",
                    "board_pins.cfg",
                    "motion.cfg",
                    "thermal.cfg",
                    "input_shaper.cfg",
                ]
            )

        for file_name in required_files:
            if file_name not in pack.files:
                report.add(
                    severity="blocking",
                    code="FILE_MISSING",
                    message=f"Missing required output file '{file_name}'.",
                )
                continue
            if not pack.files[file_name].strip():
                report.add(
                    severity="blocking",
                    code="FILE_EMPTY",
                    message=f"Output file '{file_name}' is empty.",
                )

        if layout != "source_tree":
            printer = pack.files.get("printer.cfg", "")
            for include_name in (
                "mcu.cfg",
                "board_pins.cfg",
                "motion.cfg",
                "thermal.cfg",
                "input_shaper.cfg",
            ):
                include_line = f"[include {include_name}]"
                if include_line not in printer:
                    report.add(
                        severity="blocking",
                        code="MISSING_INCLUDE",
                        message=f"'printer.cfg' does not include '{include_name}'.",
                        field="printer.cfg",
                    )

            if "macros.cfg" in pack.files and "[include macros.cfg]" not in printer:
                report.add(
                    severity="blocking",
                    code="MACRO_INCLUDE_MISSING",
                    message="'macros.cfg' was generated but not included from 'printer.cfg'.",
                    field="printer.cfg",
                )

            if "toolhead.cfg" in pack.files and "[include toolhead.cfg]" not in printer:
                report.add(
                    severity="blocking",
                    code="TOOLHEAD_INCLUDE_MISSING",
                    message="'toolhead.cfg' was generated but not included from 'printer.cfg'.",
                    field="printer.cfg",
                )

            if "[include toolhead.cfg]" in printer and "toolhead.cfg" not in pack.files:
                report.add(
                    severity="blocking",
                    code="TOOLHEAD_FILE_MISSING",
                    message="'printer.cfg' includes toolhead.cfg, but the file was not generated.",
                    field="printer.cfg",
                )

            if "toolhead_pins.cfg" in pack.files and "[include toolhead_pins.cfg]" not in printer:
                report.add(
                    severity="blocking",
                    code="TOOLHEAD_PINS_INCLUDE_MISSING",
                    message="'toolhead_pins.cfg' was generated but not included from 'printer.cfg'.",
                    field="printer.cfg",
                )

            if "leds.cfg" in pack.files and "[include leds.cfg]" not in printer:
                report.add(
                    severity="blocking",
                    code="LEDS_INCLUDE_MISSING",
                    message="'leds.cfg' was generated but not included from 'printer.cfg'.",
                    field="printer.cfg",
                )

            if "[include leds.cfg]" in printer and "leds.cfg" not in pack.files:
                report.add(
                    severity="blocking",
                    code="LEDS_FILE_MISSING",
                    message="'printer.cfg' includes leds.cfg, but the file was not generated.",
                    field="printer.cfg",
                )
        else:
            cfg_files = {
                name: content
                for name, content in pack.files.items()
                if name.lower().endswith(".cfg")
            }
            if source_root_file and source_root_file in cfg_files:
                graph_report = self.firmware_tools_service.validate_graph(
                    cfg_files,
                    source_root_file,
                )
                for finding in graph_report.findings:
                    report.add(
                        severity=finding.severity,
                        code=f"RENDER_GRAPH_{finding.code}",
                        message=finding.message,
                        field=finding.field,
                    )

        for file_name, content in pack.files.items():
            if not file_name.lower().endswith(".cfg"):
                continue
            cfg_report = self.firmware_tools_service.validate_cfg(
                content,
                source_label=file_name,
                role="auto",
            )
            for finding in cfg_report.findings:
                if (
                    finding.severity == "blocking"
                    or finding.code == "CFG_POSSIBLE_CONCATENATED_KEY_VALUE"
                ):
                    detail = finding.message
                    if finding.field:
                        detail = f"{detail} ({finding.field})"
                    report.add(
                        severity="blocking",
                        code="RENDER_CFG_SYNTAX",
                        message=f"{file_name}: {detail}",
                        field=file_name,
                    )

        return report

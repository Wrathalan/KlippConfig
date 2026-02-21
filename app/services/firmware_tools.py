from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.domain.models import ConfigFileRole, ValidationReport
from app.services.config_graph import ConfigGraphService


class FirmwareToolsService:
    SECTION_PATTERN = re.compile(r"^\s*\[([^\]]+)\]\s*(?:[#;].*)?$")
    INCLUDE_SECTION_PATTERN = re.compile(
        r"^\s*\[include\s+([^\]]+)\]\s*(?:[#;].*)?$",
        re.IGNORECASE,
    )
    KEY_VALUE_COLON_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$")
    KEY_VALUE_EQUALS_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)\s*=\s*(.*)$")

    _VALID_KINEMATICS = {
        "none",
        "cartesian",
        "corexy",
        "corexz",
        "hybrid_corexy",
        "hybrid_corexz",
        "delta",
        "deltesian",
        "polar",
        "rotary_delta",
    }
    _ROLE_VALUES = {role.value for role in ConfigFileRole}

    def __init__(self, graph_service: ConfigGraphService | None = None) -> None:
        self.graph_service = graph_service or ConfigGraphService()

    @staticmethod
    def _is_glob_pattern(path: str) -> bool:
        return any(marker in path for marker in ("*", "?", "["))

    def _include_target_looks_valid(self, target: str) -> bool:
        cleaned = target.strip().strip("'\"")
        if not cleaned:
            return False
        lowered = cleaned.lower()
        if self._is_glob_pattern(cleaned):
            return lowered.endswith(".cfg")
        return lowered.endswith(".cfg")

    @staticmethod
    def _normalize_role(role: str | ConfigFileRole) -> ConfigFileRole:
        if isinstance(role, ConfigFileRole):
            return role
        raw = str(role).strip().lower()
        if raw in FirmwareToolsService._ROLE_VALUES:
            return ConfigFileRole(raw)
        return ConfigFileRole.INCLUDE_FRAGMENT

    def classify_role(self, content: str, source_label: str = "current.cfg") -> ConfigFileRole:
        text = content or ""
        source_name = Path(source_label).name.lower()
        sections: list[str] = []
        include_count = 0
        for raw_line in text.splitlines():
            include_match = self.INCLUDE_SECTION_PATTERN.match(raw_line)
            if include_match:
                include_count += 1
                sections.append("include")
                continue
            section_match = self.SECTION_PATTERN.match(raw_line)
            if section_match:
                sections.append(section_match.group(1).strip().lower())

        section_set = set(sections)
        if source_name == "printer.cfg":
            return ConfigFileRole.ROOT
        if "printer" in section_set:
            return ConfigFileRole.ROOT
        if include_count > 0 and source_name in {"printer.cfg", "printer_main.cfg"}:
            return ConfigFileRole.ROOT

        has_macro_sections = any(
            section.startswith("gcode_macro") or section.startswith("delayed_gcode")
            for section in section_set
        )
        has_mcu_sections = any(
            section == "mcu" or section.startswith("mcu ")
            for section in section_set
        )
        if has_macro_sections and "printer" not in section_set:
            return ConfigFileRole.MACRO_PACK
        if has_mcu_sections and "printer" not in section_set:
            return ConfigFileRole.MCU_MAP
        return ConfigFileRole.INCLUDE_FRAGMENT

    def refactor_cfg(self, content: str) -> tuple[str, int]:
        original = content or ""
        lines = original.splitlines()
        output: list[str] = []
        changes = 0

        for line in lines:
            normalized = line.rstrip()

            include_match = self.INCLUDE_SECTION_PATTERN.match(normalized)
            if include_match:
                include_target = include_match.group(1).strip()
                normalized = f"[include {include_target}]"
                if output and output[-1] != "":
                    output.append("")
                    changes += 1
                output.append(normalized)
                if normalized != line:
                    changes += 1
                continue

            section_match = self.SECTION_PATTERN.match(normalized)
            if section_match:
                section_name = section_match.group(1).strip()
                normalized = f"[{section_name}]"
                if output and output[-1] != "":
                    output.append("")
                    changes += 1
                output.append(normalized)
                if normalized != line:
                    changes += 1
                continue

            stripped = normalized.strip()
            if not stripped:
                if output and output[-1] == "":
                    changes += 1
                    continue
                output.append("")
                if normalized != line:
                    changes += 1
                continue

            if not line[:1].isspace() and not stripped.startswith(("#", ";")):
                key = ""
                value = ""
                key_match = self.KEY_VALUE_COLON_PATTERN.match(stripped)
                if key_match:
                    key = key_match.group(1).strip()
                    value = key_match.group(2).strip()
                else:
                    equals_match = self.KEY_VALUE_EQUALS_PATTERN.match(stripped)
                    if equals_match:
                        key = equals_match.group(1).strip()
                        value = equals_match.group(2).strip()
                if key:
                    normalized = f"{key}: {value}" if value else f"{key}:"

            output.append(normalized)
            if normalized != line:
                changes += 1

        while output and output[-1] == "":
            output.pop()
            changes += 1

        refactored = "\n".join(output)
        if original.endswith("\n"):
            refactored += "\n"

        if refactored == original:
            return refactored, 0
        if changes == 0:
            changes = 1
        return refactored, changes

    def validate_cfg(
        self,
        content: str,
        source_label: str = "current.cfg",
        role: str | ConfigFileRole = "auto",
    ) -> ValidationReport:
        report = ValidationReport()
        lines = (content or "").splitlines()

        if not lines or not (content or "").strip():
            report.add(
                severity="blocking",
                code="CFG_EMPTY",
                message="Configuration text is empty.",
                field=source_label,
            )
            return report

        resolved_role = (
            self.classify_role(content, source_label)
            if str(role).strip().lower() == "auto"
            else self._normalize_role(role)
        )

        current_section: str | None = None
        seen_sections: Counter[str] = Counter()
        seen_keys: defaultdict[str, Counter[str]] = defaultdict(Counter)
        has_include_section = False

        for line_number, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith(("#", ";")):
                continue

            include_match = self.INCLUDE_SECTION_PATTERN.match(raw_line)
            if include_match:
                has_include_section = True
                include_target = include_match.group(1).strip()
                section_key = f"include {include_target}".lower()
                seen_sections[section_key] += 1
                current_section = f"include {include_target}"
                if not self._include_target_looks_valid(include_target):
                    report.add(
                        severity="warning",
                        code="CFG_INCLUDE_SUFFIX",
                        message=(
                            f"Include target '{include_target}' should point to '*.cfg' files."
                        ),
                        field=f"line:{line_number}",
                    )
                continue

            section_match = self.SECTION_PATTERN.match(raw_line)
            if section_match:
                current_section = section_match.group(1).strip()
                lowered_section = current_section.lower()
                seen_sections[lowered_section] += 1
                if seen_sections[lowered_section] > 1:
                    report.add(
                        severity="warning",
                        code="CFG_DUPLICATE_SECTION",
                        message=f"Section '[{current_section}]' appears multiple times.",
                        field=f"line:{line_number}",
                    )
                continue

            if stripped.startswith("["):
                report.add(
                    severity="blocking",
                    code="CFG_SECTION_SYNTAX",
                    message="Malformed section header.",
                    field=f"line:{line_number}",
                )
                current_section = None
                continue

            if raw_line[:1].isspace():
                continue

            colon_match = self.KEY_VALUE_COLON_PATTERN.match(stripped)
            equals_match = self.KEY_VALUE_EQUALS_PATTERN.match(stripped)
            if colon_match:
                key = colon_match.group(1).strip().lower()
                value = colon_match.group(2).strip()
            elif equals_match:
                key = equals_match.group(1).strip().lower()
                value = equals_match.group(2).strip()
                report.add(
                    severity="warning",
                    code="CFG_EQUALS_STYLE",
                    message=f"Use ':' instead of '=' for '{key}'.",
                    field=f"line:{line_number}",
                )
            else:
                report.add(
                    severity="warning",
                    code="CFG_UNPARSED_LINE",
                    message=f"Line could not be parsed: '{stripped}'.",
                    field=f"line:{line_number}",
                )
                continue

            if not current_section:
                report.add(
                    severity="warning",
                    code="CFG_KEY_OUTSIDE_SECTION",
                    message=f"Key '{key}' is outside of a section.",
                    field=f"line:{line_number}",
                )
                section_key = "global"
            else:
                section_key = current_section.lower()

            seen_keys[section_key][key] += 1
            if seen_keys[section_key][key] > 1:
                label = current_section or "global"
                report.add(
                    severity="warning",
                    code="CFG_DUPLICATE_KEY",
                    message=f"Key '{key}' is defined multiple times in section '[{label}]'.",
                    field=f"line:{line_number}",
                )

            if section_key == "printer":
                if key == "kinematics" and value and value.lower() not in self._VALID_KINEMATICS:
                    report.add(
                        severity="warning",
                        code="CFG_KINEMATICS_UNKNOWN",
                        message=f"Unknown kinematics value '{value}'.",
                        field=f"line:{line_number}",
                    )
                if key in {
                    "max_velocity",
                    "max_accel",
                    "max_z_velocity",
                    "max_z_accel",
                    "square_corner_velocity",
                }:
                    try:
                        numeric = float(value)
                    except ValueError:
                        report.add(
                            severity="blocking",
                            code="CFG_NUMERIC_INVALID",
                            message=f"'{key}' must be numeric.",
                            field=f"line:{line_number}",
                        )
                    else:
                        if numeric <= 0:
                            report.add(
                                severity="blocking",
                                code="CFG_NUMERIC_NON_POSITIVE",
                                message=f"'{key}' must be greater than zero.",
                                field=f"line:{line_number}",
                            )

        if resolved_role == ConfigFileRole.ROOT:
            if "printer" not in seen_sections:
                report.add(
                    severity="warning",
                    code="CFG_PRINTER_SECTION_MISSING",
                    message="No [printer] section found in this file.",
                    field=source_label,
                )

            if not has_include_section:
                for section_name in ("mcu", "extruder", "heater_bed"):
                    if section_name not in seen_sections:
                        report.add(
                            severity="warning",
                            code="CFG_COMMON_SECTION_MISSING",
                            message=f"No [{section_name}] section found.",
                            field=source_label,
                        )

        return report

    @staticmethod
    def _normalize_graph_files(files: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for path, content in files.items():
            key = path.replace("\\", "/").strip()
            if not key:
                continue
            normalized[key] = content
        return normalized

    def _detect_graph_cycles(
        self,
        graph: dict[str, list[str]],
        existing: set[str],
    ) -> list[str]:
        cycles: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()
        stack: list[str] = []

        def dfs(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                if node in stack:
                    start = stack.index(node)
                    cycles.append(" -> ".join(stack[start:] + [node]))
                return
            visiting.add(node)
            stack.append(node)
            for child in graph.get(node, []):
                if child in existing:
                    dfs(child)
            stack.pop()
            visiting.remove(node)
            visited.add(node)

        for node in graph.keys():
            if node not in visited:
                dfs(node)
        return list(dict.fromkeys(cycles))

    def _collect_cross_file_conflicts(
        self,
        files: dict[str, str],
        order: list[str],
    ) -> list[tuple[str, str, str, str]]:
        seen: dict[tuple[str, str], tuple[str, str]] = {}
        conflicts: list[tuple[str, str, str, str]] = []
        for path in order:
            content = files.get(path)
            if content is None:
                continue
            current_section: str | None = None
            for raw_line in content.splitlines():
                include_match = self.INCLUDE_SECTION_PATTERN.match(raw_line)
                if include_match:
                    current_section = f"include {include_match.group(1).strip()}"
                    continue
                section_match = self.SECTION_PATTERN.match(raw_line)
                if section_match:
                    current_section = section_match.group(1).strip().lower()
                    continue
                stripped = raw_line.strip()
                if not stripped or stripped.startswith(("#", ";")) or raw_line[:1].isspace():
                    continue
                key_match = self.KEY_VALUE_COLON_PATTERN.match(stripped)
                if not key_match:
                    key_match = self.KEY_VALUE_EQUALS_PATTERN.match(stripped)
                if not key_match or not current_section:
                    continue
                key = key_match.group(1).strip().lower()
                value = key_match.group(2).strip()
                marker = (current_section, key)
                previous = seen.get(marker)
                if previous and previous[0] != value and previous[1] != path:
                    conflicts.append((current_section, key, previous[1], path))
                else:
                    seen[marker] = (value, path)
        return conflicts

    def validate_graph(self, files: dict[str, str], root_file: str) -> ValidationReport:
        report = ValidationReport()
        normalized_files = self._normalize_graph_files(files)
        normalized_root = root_file.replace("\\", "/").strip()

        if not normalized_files:
            report.add(
                severity="blocking",
                code="CFG_GRAPH_EMPTY",
                message="No configuration files provided for graph validation.",
                field=normalized_root or "printer.cfg",
            )
            return report

        if normalized_root not in normalized_files:
            report.add(
                severity="blocking",
                code="CFG_ROOT_MISSING",
                message=f"Root file '{normalized_root}' was not found in provided files.",
                field=normalized_root or "printer.cfg",
            )
            return report

        graph = self.graph_service.build_graph(normalized_files, normalized_root)
        order = self.graph_service.flatten_graph(graph, normalized_root)
        existing_files = set(normalized_files.keys())

        for source, targets in graph.items():
            for target in targets:
                if target in existing_files:
                    continue
                report.add(
                    severity="warning",
                    code="CFG_INCLUDE_MISSING",
                    message=f"Unresolved include target '{target}' referenced by '{source}'.",
                    field=source,
                )

        for cycle in self._detect_graph_cycles(graph, existing_files):
            report.add(
                severity="warning",
                code="CFG_INCLUDE_CYCLE",
                message=f"Include cycle detected: {cycle}",
                field=normalized_root,
            )

        for index, file_path in enumerate(order):
            content = normalized_files.get(file_path, "")
            if not content:
                continue
            role: str | ConfigFileRole = (
                ConfigFileRole.ROOT if index == 0 and file_path == normalized_root else "auto"
            )
            file_report = self.validate_cfg(content, source_label=file_path, role=role)
            for finding in file_report.findings:
                field = finding.field
                if field and field.startswith("line:"):
                    field = f"{file_path}:{field}"
                elif not field:
                    field = file_path
                report.add(
                    severity=finding.severity,
                    code=finding.code,
                    message=finding.message,
                    field=field,
                )

        for section, key, old_path, new_path in self._collect_cross_file_conflicts(
            normalized_files,
            order,
        ):
            report.add(
                severity="warning",
                code="CFG_GRAPH_DUPLICATE_KEY",
                message=(
                    f"Key '{key}' in section '[{section}]' is defined in multiple files: "
                    f"{old_path} and {new_path}."
                ),
                field=f"{new_path}:{section}.{key}",
            )

        return report

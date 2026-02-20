from __future__ import annotations

import re
from collections import Counter, defaultdict

from app.domain.models import ValidationReport


class FirmwareToolsService:
    SECTION_PATTERN = re.compile(r"^\s*\[([^\]]+)\]\s*$")
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

    def refactor_cfg(self, content: str) -> tuple[str, int]:
        original = content or ""
        lines = original.splitlines()
        output: list[str] = []
        changes = 0

        for line in lines:
            normalized = line.rstrip()

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

    def validate_cfg(self, content: str, source_label: str = "current.cfg") -> ValidationReport:
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

        current_section: str | None = None
        seen_sections: Counter[str] = Counter()
        seen_keys: defaultdict[str, Counter[str]] = defaultdict(Counter)
        has_include_section = False

        for line_number, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith(("#", ";")):
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
                if lowered_section.startswith("include "):
                    has_include_section = True
                    include_target = current_section[8:].strip()
                    if include_target and not include_target.lower().endswith(".cfg"):
                        report.add(
                            severity="warning",
                            code="CFG_INCLUDE_SUFFIX",
                            message=(
                                f"Include target '{include_target}' does not end with '.cfg'."
                            ),
                            field=f"line:{line_number}",
                        )
                continue

            if stripped.startswith("[") and not section_match:
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

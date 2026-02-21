from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any

from app.domain.models import ImportSuggestion, ImportedMachineProfile, ProjectConfig
from app.services.board_registry import (
    get_board_profile,
    list_main_boards,
)
from app.services.config_graph import ConfigGraphService


class ExistingMachineImportError(Exception):
    """Raised when import or analysis of existing machine config fails."""


class ExistingMachineImportService:
    SECTION_PATTERN = re.compile(r"^\s*\[([^\]]+)\]\s*(?:[#;].*)?$")
    KEY_VALUE_PATTERN = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*[:=]\s*(.*)$")
    INCLUDE_PATTERN = re.compile(r"^\s*\[include\s+([^\]]+)\]\s*(?:[#;].*)?$", re.IGNORECASE)
    HIGH_CONFIDENCE_THRESHOLD = 0.85

    def __init__(
        self,
        graph_service: ConfigGraphService | None = None,
        high_confidence_threshold: float = HIGH_CONFIDENCE_THRESHOLD,
    ) -> None:
        self.graph_service = graph_service or ConfigGraphService()
        self.high_confidence_threshold = max(0.5, min(0.99, float(high_confidence_threshold)))
        self.last_import_files: dict[str, str] = {}

    @staticmethod
    def _normalize_path(path: str) -> str:
        raw = path.replace("\\", "/").strip()
        if not raw:
            return ""
        normalized = posixpath.normpath(raw)
        if raw.startswith("/") and not normalized.startswith("/"):
            return f"/{normalized}"
        return normalized

    @staticmethod
    def _read_text_file(path: Path) -> str:
        raw = path.read_bytes()
        for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def _read_zip_files(self, path: Path) -> dict[str, str]:
        files: dict[str, str] = {}
        with zipfile.ZipFile(path, "r") as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                normalized = self._normalize_path(info.filename)
                if not normalized:
                    continue
                raw = archive.read(info)
                text = raw.decode("utf-8", errors="replace")
                files[normalized] = text
        return files

    def _read_folder_files(self, folder: Path) -> dict[str, str]:
        files: dict[str, str] = {}
        for file_path in sorted(folder.rglob("*")):
            if not file_path.is_file():
                continue
            relative = self._normalize_path(str(file_path.relative_to(folder)))
            if not relative:
                continue
            files[relative] = self._read_text_file(file_path)
        return files

    @staticmethod
    def _make_profile_name(path: Path) -> str:
        name = path.stem if path.is_file() else path.name
        clean = re.sub(r"[^A-Za-z0-9_. -]+", "_", name).strip()
        return clean or "Imported Machine"

    def _detect_root_file(self, files: dict[str, str]) -> str:
        normalized = {self._normalize_path(path): content for path, content in files.items()}
        preferred = ("config/printer.cfg", "printer.cfg")
        for candidate in preferred:
            if candidate in normalized:
                return candidate

        for path, content in normalized.items():
            if re.search(r"^\s*\[printer\]\s*(?:[#;].*)?$", content, flags=re.MULTILINE):
                return path

        for path, content in normalized.items():
            if "[include " in content.lower():
                return path

        if not normalized:
            raise ExistingMachineImportError("No files found in source.")
        return sorted(normalized.keys())[0]

    def _parse_sections(self, content: str) -> dict[str, dict[str, str]]:
        sections: dict[str, dict[str, str]] = {}
        current_section: str | None = None
        for raw_line in (content or "").splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith(("#", ";")):
                continue
            section_match = self.SECTION_PATTERN.match(raw_line)
            if section_match:
                current_section = section_match.group(1).strip().lower()
                sections.setdefault(current_section, {})
                continue
            if raw_line[:1].isspace():
                continue
            key_match = self.KEY_VALUE_PATTERN.match(raw_line)
            if key_match and current_section:
                key = key_match.group(1).strip().lower()
                value = key_match.group(2).strip()
                sections.setdefault(current_section, {})[key] = value
        return sections

    @staticmethod
    def _as_float(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return float(value.strip())
        except (TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def _nearest_build_size(x: float | None, y: float | None) -> int | None:
        if x is None and y is None:
            return None
        samples = [size for size in (x, y) if size is not None]
        if not samples:
            return None
        estimate = min(samples)
        options = (250, 300, 350)
        return min(options, key=lambda candidate: abs(candidate - estimate))

    def _merge_section_maps(
        self,
        files: dict[str, str],
        include_graph: dict[str, list[str]],
        root_file: str,
    ) -> dict[str, dict[str, str]]:
        merged: dict[str, dict[str, str]] = {}
        flattened = self.graph_service.flatten_graph(include_graph, root_file)
        for file_path in flattened:
            content = files.get(file_path)
            if content is None:
                continue
            sections = self._parse_sections(content)
            for section_name, values in sections.items():
                merged.setdefault(section_name, {})
                merged[section_name].update(values)
        return merged

    def _score_mainboard(
        self,
        combined_text: str,
        serial_hint: str,
        merged_sections: dict[str, dict[str, str]],
    ) -> tuple[str | None, float, str]:
        section_pin_map = {
            "stepper_x_step": merged_sections.get("stepper_x", {}).get("step_pin"),
            "stepper_y_step": merged_sections.get("stepper_y", {}).get("step_pin"),
            "stepper_z_step": merged_sections.get("stepper_z", {}).get("step_pin"),
            "heater_bed": merged_sections.get("heater_bed", {}).get("heater_pin"),
            "temp_bed": merged_sections.get("heater_bed", {}).get("sensor_pin"),
            "probe": merged_sections.get("probe", {}).get("pin"),
        }

        best_id: str | None = None
        best_confidence = 0.0
        best_reason = ""

        for board_id in list_main_boards():
            profile = get_board_profile(board_id)
            if profile is None:
                continue
            score = 0.0
            reasons: list[str] = []

            if serial_hint and profile.mcu.lower() in serial_hint:
                score += 0.55
                reasons.append(f"MCU serial suggests {profile.mcu}.")

            signature_matches = 0
            for key, detected_pin in section_pin_map.items():
                if not detected_pin:
                    continue
                expected = profile.pins.get(key)
                if not expected:
                    continue
                normalized_detected = detected_pin.replace("!", "").strip().lower()
                if expected.strip().lower() == normalized_detected:
                    signature_matches += 1
            if signature_matches > 0:
                signature_boost = min(0.4, 0.08 + (signature_matches * 0.08))
                score += signature_boost
                reasons.append(f"Matched {signature_matches} known pin signatures.")

            if board_id.replace("_", " ") in combined_text:
                score += 0.1
                reasons.append("Board id mention found in config text.")

            if score > best_confidence:
                best_confidence = score
                best_id = board_id
                best_reason = " ".join(reasons) if reasons else "Closest known board signature."

        return best_id, min(0.99, best_confidence), best_reason or "Closest known board signature."

    def _detect_addons(
        self,
        files: dict[str, str],
        include_graph: dict[str, list[str]],
        combined_text: str,
    ) -> list[tuple[str, float, str, str]]:
        addons: dict[str, tuple[float, str, str]] = {}
        lowered_text = combined_text.lower()
        file_blob = "\n".join(files.keys()).lower()

        def mark(addon_id: str, confidence: float, reason: str, source_file: str) -> None:
            existing = addons.get(addon_id)
            if existing is None or confidence > existing[0]:
                addons[addon_id] = (confidence, reason, source_file)

        for file_path, content in files.items():
            for raw_line in content.splitlines():
                include_match = self.INCLUDE_PATTERN.match(raw_line)
                if not include_match:
                    continue
                include_value = include_match.group(1).strip().lower()
                if "kamp" in include_value:
                    mark("kamp", 0.92, "KAMP include chain detected.", file_path)
                if "afc/" in include_value or include_value.startswith("afc"):
                    mark("afc", 0.95, "AFC include chain detected.", file_path)
                if "stealthburner_leds.cfg" in include_value:
                    mark(
                        "stealthburner_leds",
                        0.91,
                        "Stealthburner LED include detected.",
                        file_path,
                    )
                if "timelapse.cfg" in include_value:
                    mark("timelapse", 0.9, "Timelapse include detected.", file_path)

            lowered = content.lower()
            if "[afc]" in lowered or "[afc_prep]" in lowered:
                mark("afc", 0.95, "AFC macro sections detected.", file_path)
            if "[afc_boxturtle" in lowered:
                mark("box_turtle", 0.94, "AFC BoxTurtle section detected.", file_path)
            if "[gcode_macro _kamp_settings]" in lowered:
                mark("kamp", 0.9, "KAMP settings macro detected.", file_path)
            if "[neopixel sb_leds]" in lowered:
                mark(
                    "stealthburner_leds",
                    0.93,
                    "Stealthburner LED section detected.",
                    file_path,
                )
            if "[gcode_macro timelapse_take_frame]" in lowered:
                mark("timelapse", 0.92, "Timelapse macro sections detected.", file_path)

        if "afc_boxturtle" in lowered_text:
            mark("box_turtle", 0.95, "AFC_BoxTurtle markers detected.", "graph")
        if "afc" in lowered_text and "box_turtle" not in addons:
            mark("afc", 0.86, "AFC markers detected.", "graph")
        if "kamp" in lowered_text or "kamp" in file_blob:
            mark("kamp", 0.86, "KAMP markers detected.", "graph")
        if "timelapse" in lowered_text or "timelapse.cfg" in file_blob:
            mark("timelapse", 0.86, "Timelapse markers detected.", "graph")

        return sorted(
            [(addon_id, conf, reason, source) for addon_id, (conf, reason, source) in addons.items()],
            key=lambda item: item[0],
        )

    def _detect_traits(
        self,
        files: dict[str, str],
        root_file: str,
        include_graph: dict[str, list[str]],
    ) -> dict[str, Any]:
        flattened = self.graph_service.flatten_graph(include_graph, root_file)
        flattened_files = [path for path in flattened if path in files]
        combined_text = "\n".join(files[path] for path in flattened_files).lower()
        merged_sections = self._merge_section_maps(files, include_graph, root_file)

        printer_section = merged_sections.get("printer", {})
        kinematics = printer_section.get("kinematics", "").strip().lower()
        has_qgl = "quad_gantry_level" in merged_sections

        z_stepper_count = sum(
            1
            for section_name in merged_sections.keys()
            if section_name == "stepper_z" or re.fullmatch(r"stepper_z\d+", section_name)
        )
        x_max = self._as_float(merged_sections.get("stepper_x", {}).get("position_max"))
        if x_max is None:
            x_max = self._as_float(merged_sections.get("stepper_x", {}).get("position_endstop"))
        y_max = self._as_float(merged_sections.get("stepper_y", {}).get("position_max"))
        if y_max is None:
            y_max = self._as_float(merged_sections.get("stepper_y", {}).get("position_endstop"))
        z_max = self._as_float(merged_sections.get("stepper_z", {}).get("position_max"))

        inferred_xy = self._nearest_build_size(x_max, y_max)
        inferred_z = int(round(z_max)) if z_max is not None else None
        if inferred_z is None and inferred_xy in {250, 300, 350}:
            inferred_z = 300 if inferred_xy in {250, 300} else 330

        preset_id: str | None = None
        preset_confidence = 0.0
        preset_reason = ""
        if kinematics == "corexy" and has_qgl and z_stepper_count >= 4:
            size = inferred_xy or 300
            preset_id = f"voron_2_4_{size}"
            preset_confidence = 0.93
            preset_reason = (
                "Detected CoreXY + [quad_gantry_level] + four Z steppers, matching Voron 2.4."
            )

        serial_hint = merged_sections.get("mcu", {}).get("serial", "").strip().lower()
        board_id, board_confidence, board_reason = self._score_mainboard(
            combined_text=combined_text,
            serial_hint=serial_hint,
            merged_sections=merged_sections,
        )

        toolhead_detected = False
        toolhead_board: str | None = None
        toolhead_confidence = 0.0
        toolhead_reason = ""
        toolhead_source = root_file
        canbus_uuid = None

        for section_name, section_values in merged_sections.items():
            if section_name.startswith("mcu "):
                suffix = section_name.split(" ", 1)[1].strip()
                if suffix == "nhk":
                    toolhead_detected = True
                    toolhead_board = "ldo_nitehawk_sb"
                    toolhead_confidence = 0.96
                    toolhead_reason = "[mcu nhk] detected."
                    toolhead_source = root_file
                maybe_uuid = section_values.get("canbus_uuid")
                if maybe_uuid:
                    canbus_uuid = maybe_uuid.strip()
        if "nhk:gpio" in combined_text or "nitehawk" in combined_text:
            toolhead_detected = True
            if not toolhead_board:
                toolhead_board = "ldo_nitehawk_sb"
            toolhead_confidence = max(toolhead_confidence, 0.93)
            if not toolhead_reason:
                toolhead_reason = "Nitehawk/nhk pin aliases detected."

        addons = self._detect_addons(files, include_graph, combined_text)
        if any(addon_id == "box_turtle" for addon_id, *_ in addons):
            # AFC BoxTurtle implies AFC stack, but avoid a multi-material conflict by only
            # auto-applying box_turtle and keeping AFC as manual when both are present.
            adjusted: list[tuple[str, float, str, str]] = []
            for addon_id, conf, reason, source in addons:
                if addon_id == "afc":
                    adjusted.append((addon_id, min(conf, 0.84), reason, source))
                else:
                    adjusted.append((addon_id, conf, reason, source))
            addons = adjusted

        probe_enabled = "probe" in merged_sections
        probe_type: str | None = None
        if probe_enabled:
            probe_pin = (merged_sections.get("probe", {}).get("pin") or "").lower()
            if "tap" in combined_text:
                probe_type = "tap"
            elif "bltouch" in combined_text:
                probe_type = "bltouch"
            elif "klicky" in combined_text:
                probe_type = "klicky"
            elif "nhk:" in probe_pin or "gpio" in probe_pin:
                probe_type = "inductive"

        dimensions = {
            "x": int(inferred_xy) if inferred_xy else None,
            "y": int(inferred_xy) if inferred_xy else None,
            "z": int(inferred_z) if inferred_z else None,
        }

        return {
            "root_file": root_file,
            "kinematics": kinematics or None,
            "has_quad_gantry_level": has_qgl,
            "z_stepper_count": z_stepper_count,
            "dimensions": dimensions,
            "preset_id": preset_id,
            "preset_confidence": preset_confidence,
            "preset_reason": preset_reason,
            "board_id": board_id,
            "board_confidence": board_confidence,
            "board_reason": board_reason,
            "toolhead": {
                "enabled": toolhead_detected,
                "board": toolhead_board,
                "confidence": toolhead_confidence,
                "reason": toolhead_reason,
                "source_file": toolhead_source,
                "canbus_uuid": canbus_uuid,
            },
            "probe": {
                "enabled": probe_enabled,
                "type": probe_type,
            },
            "addons": [
                {
                    "id": addon_id,
                    "confidence": confidence,
                    "reason": reason,
                    "source_file": source_file,
                }
                for addon_id, confidence, reason, source_file in addons
            ],
        }

    def _analysis_warnings(
        self,
        files: dict[str, str],
        include_graph: dict[str, list[str]],
    ) -> list[str]:
        warnings: list[str] = []
        known_files = set(files.keys())

        for source, targets in include_graph.items():
            for target in targets:
                if target in known_files:
                    continue
                warnings.append(f"{source}: include target not found: {target}")

        stack: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                cycle_start = stack.index(node) if node in stack else 0
                cycle = " -> ".join(stack[cycle_start:] + [node])
                warnings.append(f"Include cycle detected: {cycle}")
                return
            visiting.add(node)
            stack.append(node)
            for child in include_graph.get(node, []):
                if child in include_graph:
                    dfs(child)
            stack.pop()
            visiting.remove(node)
            visited.add(node)

        for root in include_graph.keys():
            if root not in visited:
                dfs(root)

        return list(dict.fromkeys(warnings))

    def _make_suggestion(
        self,
        *,
        field: str,
        value: Any,
        confidence: float,
        reason: str,
        source_file: str,
    ) -> ImportSuggestion:
        confidence_value = max(0.0, min(1.0, float(confidence)))
        return ImportSuggestion(
            field=field,
            value=value,
            confidence=confidence_value,
            reason=reason,
            source_file=source_file,
            auto_apply=confidence_value >= self.high_confidence_threshold,
        )

    def _build_suggestions(self, detected: dict[str, Any], root_file: str) -> list[ImportSuggestion]:
        suggestions: list[ImportSuggestion] = []

        preset_id = detected.get("preset_id")
        if isinstance(preset_id, str) and preset_id:
            suggestions.append(
                self._make_suggestion(
                    field="preset_id",
                    value=preset_id,
                    confidence=float(detected.get("preset_confidence") or 0.0),
                    reason=str(detected.get("preset_reason") or "Detected machine family."),
                    source_file=root_file,
                )
            )

        board_id = detected.get("board_id")
        if isinstance(board_id, str) and board_id:
            suggestions.append(
                self._make_suggestion(
                    field="board",
                    value=board_id,
                    confidence=float(detected.get("board_confidence") or 0.0),
                    reason=str(detected.get("board_reason") or "Detected mainboard signature."),
                    source_file=root_file,
                )
            )

        dimensions = detected.get("dimensions")
        if isinstance(dimensions, dict):
            for axis in ("x", "y", "z"):
                value = dimensions.get(axis)
                if isinstance(value, int) and value > 0:
                    confidence = 0.9 if axis in {"x", "y"} else 0.8
                    suggestions.append(
                        self._make_suggestion(
                            field=f"dimensions.{axis}",
                            value=value,
                            confidence=confidence,
                            reason=f"Detected axis {axis.upper()} bounds from motion settings.",
                            source_file=root_file,
                        )
                    )

        probe = detected.get("probe")
        if isinstance(probe, dict):
            enabled = bool(probe.get("enabled"))
            suggestions.append(
                self._make_suggestion(
                    field="probe.enabled",
                    value=enabled,
                    confidence=0.9 if enabled else 0.8,
                    reason="[probe] section presence detected.",
                    source_file=root_file,
                )
            )
            probe_type = probe.get("type")
            if isinstance(probe_type, str) and probe_type:
                suggestions.append(
                    self._make_suggestion(
                        field="probe.type",
                        value=probe_type,
                        confidence=0.82,
                        reason="Probe type inferred from existing config markers.",
                        source_file=root_file,
                    )
                )

        toolhead = detected.get("toolhead")
        if isinstance(toolhead, dict) and bool(toolhead.get("enabled")):
            source = str(toolhead.get("source_file") or root_file)
            confidence = float(toolhead.get("confidence") or 0.0)
            reason = str(toolhead.get("reason") or "Toolhead board markers detected.")
            suggestions.append(
                self._make_suggestion(
                    field="toolhead.enabled",
                    value=True,
                    confidence=max(confidence, 0.9),
                    reason=reason,
                    source_file=source,
                )
            )
            board = toolhead.get("board")
            if isinstance(board, str) and board:
                suggestions.append(
                    self._make_suggestion(
                        field="toolhead.board",
                        value=board,
                        confidence=max(confidence, 0.9),
                        reason=reason,
                        source_file=source,
                    )
                )
            canbus_uuid = toolhead.get("canbus_uuid")
            if isinstance(canbus_uuid, str) and canbus_uuid.strip():
                suggestions.append(
                    self._make_suggestion(
                        field="toolhead.canbus_uuid",
                        value=canbus_uuid.strip(),
                        confidence=0.88,
                        reason="Existing toolhead canbus_uuid detected.",
                        source_file=source,
                    )
                )

        addons = detected.get("addons")
        if isinstance(addons, list):
            for addon in addons:
                if not isinstance(addon, dict):
                    continue
                addon_id = addon.get("id")
                if not isinstance(addon_id, str) or not addon_id:
                    continue
                suggestions.append(
                    self._make_suggestion(
                        field="addons",
                        value=addon_id,
                        confidence=float(addon.get("confidence") or 0.0),
                        reason=str(addon.get("reason") or "Add-on marker detected."),
                        source_file=str(addon.get("source_file") or root_file),
                    )
                )

        suggestions.sort(
            key=lambda suggestion: (
                -suggestion.confidence,
                suggestion.field,
                str(suggestion.value),
            )
        )
        return suggestions

    def _analyze_files(
        self,
        *,
        source_name: str,
        source_kind: str,
        files: dict[str, str],
    ) -> ImportedMachineProfile:
        normalized_files = {
            self._normalize_path(path): content for path, content in files.items() if self._normalize_path(path)
        }
        if not normalized_files:
            raise ExistingMachineImportError("Import source contains no readable files.")

        root_file = self._detect_root_file(normalized_files)
        include_graph = self.graph_service.build_graph(normalized_files, root_file)
        detected = self._detect_traits(normalized_files, root_file, include_graph)
        detected["file_map"] = normalized_files
        detected["include_order"] = self.graph_service.flatten_graph(include_graph, root_file)
        warnings = self._analysis_warnings(normalized_files, include_graph)
        suggestions = self._build_suggestions(detected, root_file)

        profile = ImportedMachineProfile(
            name=source_name,
            root_file=root_file,
            source_kind="zip" if source_kind == "zip" else "folder",
            detected=detected,
            suggestions=suggestions,
            include_graph=include_graph,
            analysis_warnings=warnings,
        )
        self.last_import_files = normalized_files
        return profile

    def import_zip(self, path: str) -> ImportedMachineProfile:
        zip_path = Path(path).expanduser()
        if not zip_path.exists() or not zip_path.is_file():
            raise ExistingMachineImportError(f"ZIP source not found: {zip_path}")
        if zip_path.suffix.lower() != ".zip":
            raise ExistingMachineImportError("Import ZIP path must end with '.zip'.")
        files = self._read_zip_files(zip_path)
        return self._analyze_files(
            source_name=self._make_profile_name(zip_path),
            source_kind="zip",
            files=files,
        )

    def import_folder(self, path: str) -> ImportedMachineProfile:
        folder_path = Path(path).expanduser()
        if not folder_path.exists() or not folder_path.is_dir():
            raise ExistingMachineImportError(f"Folder source not found: {folder_path}")
        files = self._read_folder_files(folder_path)
        return self._analyze_files(
            source_name=self._make_profile_name(folder_path),
            source_kind="folder",
            files=files,
        )

    @staticmethod
    def _set_nested_value(data: dict[str, Any], field: str, value: Any) -> None:
        path = [part for part in field.split(".") if part]
        if not path:
            return
        cursor: Any = data
        for part in path[:-1]:
            if part not in cursor or not isinstance(cursor[part], dict):
                cursor[part] = {}
            cursor = cursor[part]
        cursor[path[-1]] = value

    def apply_suggestions(
        self,
        profile: ImportedMachineProfile,
        project: ProjectConfig,
    ) -> ProjectConfig:
        updated = project.model_dump(mode="json")
        selected = [suggestion for suggestion in profile.suggestions if suggestion.auto_apply]
        if not selected:
            return project

        addons = set(str(item) for item in updated.get("addons", []) if isinstance(item, str))
        for suggestion in selected:
            if suggestion.field == "addons":
                value = suggestion.value
                if isinstance(value, str) and value:
                    addons.add(value)
                elif isinstance(value, list):
                    addons.update(str(item) for item in value if str(item).strip())
                continue
            self._set_nested_value(updated, suggestion.field, suggestion.value)

        if addons:
            updated["addons"] = sorted(addons)

        return ProjectConfig.model_validate(updated)

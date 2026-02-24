from __future__ import annotations

import json
from pathlib import Path

from app.domain.models import ProjectConfig


class ProjectStoreService:
    CURRENT_SCHEMA_VERSION = 2

    @staticmethod
    def _coerce_float(value: object, default: float) -> float:
        try:
            result = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default
        return result if result > 0 else default

    def _migrate_v1_to_v2(self, data: dict) -> dict:
        migrated = dict(data)
        overrides = migrated.get("advanced_overrides")
        if not isinstance(overrides, dict):
            overrides = {}

        toolhead = migrated.get("toolhead")
        if not isinstance(toolhead, dict):
            toolhead = {}

        mcu_serial = overrides.get("mcu.serial")
        toolhead_serial = overrides.get("toolhead.serial")
        toolhead_uuid = toolhead.get("canbus_uuid") or overrides.get("toolhead.canbus_uuid")

        mcu_map: dict[str, dict[str, object]] = {
            "mcu": {
                "serial": str(mcu_serial) if mcu_serial is not None else None,
                "canbus_uuid": None,
                "restart_method": "command",
            }
        }
        if toolhead.get("board"):
            mcu_map["toolhead"] = {
                "serial": str(toolhead_serial) if toolhead_serial is not None else None,
                "canbus_uuid": str(toolhead_uuid) if toolhead_uuid is not None else None,
                "restart_method": "command",
            }

        printer_limits = {
            "max_velocity": self._coerce_float(overrides.get("motion.max_velocity"), 300.0),
            "max_accel": self._coerce_float(overrides.get("motion.max_accel"), 3000.0),
            "max_z_velocity": (
                self._coerce_float(overrides.get("motion.max_z_velocity"), 15.0)
                if overrides.get("motion.max_z_velocity") is not None
                else None
            ),
            "max_z_accel": (
                self._coerce_float(overrides.get("motion.max_z_accel"), 350.0)
                if overrides.get("motion.max_z_accel") is not None
                else None
            ),
            "square_corner_velocity": self._coerce_float(
                overrides.get("motion.square_corner_velocity"), 5.0
            ),
        }

        migrated["schema_version"] = self.CURRENT_SCHEMA_VERSION
        migrated.setdefault("output_layout", "source_tree")
        migrated.setdefault(
            "machine_attributes",
            {
                "root_file": "printer.cfg",
                "include_graph": {},
                "printer_limits": printer_limits,
                "mcu_map": mcu_map,
                "stepper_sections": {},
                "driver_sections": {},
                "probe_sections": {},
                "leveling_sections": {},
                "thermal_sections": {},
                "fan_sections": {},
                "sensor_sections": {},
                "resonance_sections": {},
            },
        )
        migrated.setdefault(
            "addon_configs",
            {
                "kamp": {"enabled": False, "include_files": [], "sections": {}},
                "stealthburner_leds": {"enabled": False, "include_files": [], "sections": {}},
                "timelapse": {"enabled": False, "include_files": [], "sections": {}},
            },
        )
        migrated.setdefault("section_map", {})
        return migrated

    @staticmethod
    def _sanitize_removed_addons(data: dict) -> dict:
        sanitized = dict(data)
        addons = sanitized.get("addons")
        if isinstance(addons, list):
            sanitized["addons"] = [
                item
                for item in addons
                if not (isinstance(item, str) and item.strip().lower() == "afc")
            ]
        addon_configs = sanitized.get("addon_configs")
        if isinstance(addon_configs, dict) and "afc" in addon_configs:
            sanitized_addon_configs = dict(addon_configs)
            sanitized_addon_configs.pop("afc", None)
            sanitized["addon_configs"] = sanitized_addon_configs
        return sanitized

    def save(self, path: str, project: ProjectConfig) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = project.model_dump(mode="json")
        payload["schema_version"] = self.CURRENT_SCHEMA_VERSION
        target.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def load(self, path: str) -> ProjectConfig:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Project file payload must be an object.")
        schema_version = data.get("schema_version")
        if schema_version != self.CURRENT_SCHEMA_VERSION:
            data = self._migrate_v1_to_v2(data)
        data = self._sanitize_removed_addons(data)
        return ProjectConfig.model_validate(data)

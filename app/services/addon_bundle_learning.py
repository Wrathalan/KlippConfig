from __future__ import annotations

import json
from pathlib import Path

from app.domain.models import ImportedMachineProfile
from app.services.paths import user_bundles_dir


class AddonBundleLearningService:
    """Learns add-on package bundles from imported printer configs."""

    SUPPORTED_ADDONS = ("kamp", "stealthburner_leds", "timelapse")

    def __init__(self, bundle_root: Path | None = None) -> None:
        self.bundle_root = (bundle_root or user_bundles_dir()).expanduser()

    @staticmethod
    def _normalize_path(path: str) -> str:
        return path.replace("\\", "/").strip().lstrip("/")

    @staticmethod
    def _to_output_path(path: str) -> str:
        normalized = AddonBundleLearningService._normalize_path(path)
        if normalized.lower().startswith("config/"):
            return normalized.split("/", 1)[1]
        return normalized

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    def _detect_addon_file_map(self, file_map: dict[str, str]) -> dict[str, dict[str, str]]:
        grouped: dict[str, dict[str, str]] = {addon_id: {} for addon_id in self.SUPPORTED_ADDONS}
        for file_path, content in file_map.items():
            normalized = self._normalize_path(file_path)
            lowered = normalized.lower()
            if "/kamp/" in lowered or lowered.endswith("kamp_settings.cfg"):
                grouped["kamp"][normalized] = content
            elif lowered.endswith("stealthburner_leds.cfg"):
                grouped["stealthburner_leds"][normalized] = content
            elif lowered.endswith("timelapse.cfg"):
                grouped["timelapse"][normalized] = content
        return grouped

    @staticmethod
    def _default_include_files(addon_id: str, files: dict[str, str]) -> list[str]:
        normalized = [AddonBundleLearningService._to_output_path(path) for path in files]
        lowered = {path.lower(): path for path in normalized}
        if addon_id == "kamp":
            if "kamp_settings.cfg" in lowered:
                return [lowered["kamp_settings.cfg"]]
        if addon_id == "stealthburner_leds":
            if "stealthburner_leds.cfg" in lowered:
                return [lowered["stealthburner_leds.cfg"]]
        if addon_id == "timelapse":
            if "timelapse.cfg" in lowered:
                return [lowered["timelapse.cfg"]]
        return [normalized[0]] if normalized else []

    def learn_from_import(
        self,
        profile: ImportedMachineProfile,
        file_map: dict[str, str],
    ) -> list[Path]:
        if not file_map:
            return []

        addon_files = self._detect_addon_file_map(file_map)
        created: list[Path] = []
        addons_dir = self.bundle_root / "addons"
        templates_dir = self.bundle_root / "templates"
        addons_dir.mkdir(parents=True, exist_ok=True)
        templates_dir.mkdir(parents=True, exist_ok=True)

        for addon_id, files in addon_files.items():
            if not files:
                continue

            package_templates: dict[str, str] = {}
            include_files = self._default_include_files(addon_id, files)
            output_files = [self._to_output_path(path) for path in files]
            output_files = self._dedupe([path for path in output_files if path])

            for source_path, content in files.items():
                output_path = self._to_output_path(source_path)
                template_rel = f"addons/learned/{addon_id}/{output_path}.j2"
                template_path = templates_dir / Path(*template_rel.split("/"))
                template_path.parent.mkdir(parents=True, exist_ok=True)
                template_path.write_text(content, encoding="utf-8")
                package_templates[output_path] = template_rel
                created.append(template_path)

            default_template = (
                package_templates.get(include_files[0])
                if include_files
                else next(iter(package_templates.values()))
            )
            addon_payload = {
                "id": addon_id,
                "label": f"{addon_id.replace('_', ' ').title()} (Learned)",
                "template": default_template,
                "description": f"Learned from imported machine profile '{profile.name}'.",
                "supported_families": ["voron"],
                "multi_material": False,
                "recommends_toolhead": False,
                "learned": True,
                "include_files": include_files,
                "package_templates": package_templates,
                "output_files": output_files,
            }
            addon_json_path = addons_dir / f"{addon_id}.json"
            addon_json_path.write_text(
                json.dumps(addon_payload, indent=2, sort_keys=False) + "\n",
                encoding="utf-8",
            )
            created.append(addon_json_path)

        return created

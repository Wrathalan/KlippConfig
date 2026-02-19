from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from app.domain.models import Preset, PresetSummary
from app.services.paths import presets_dir as default_presets_dir
from app.services.paths import schemas_dir as default_schemas_dir


class PresetCatalogError(Exception):
    """Raised when preset loading fails."""


class PresetCatalogService:
    def __init__(
        self,
        preset_root: Path | None = None,
        schema_root: Path | None = None,
    ) -> None:
        self.preset_root = preset_root or default_presets_dir()
        self.schema_root = schema_root or default_schemas_dir()
        self._preset_cache: dict[str, Preset] = {}
        self._preset_schema = self._read_json(self.schema_root / "preset.schema.json")
        self._project_schema = self._read_json(self.schema_root / "project_config.schema.json")
        self._preset_validator = Draft202012Validator(self._preset_schema)
        self._project_validator = Draft202012Validator(self._project_schema)

    @staticmethod
    def _read_json(path: Path) -> dict:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _iter_preset_files(self) -> list[Path]:
        explicit_index = self.preset_root / "index.json"
        if explicit_index.exists():
            index_data = self._read_json(explicit_index)
            ids = index_data.get("presets", [])
            files: list[Path] = []
            for preset_id in ids:
                path = self.preset_root / f"{preset_id}.json"
                if not path.exists():
                    raise PresetCatalogError(f"Preset listed in index but missing on disk: {preset_id}")
                files.append(path)
            return files
        return sorted(
            p
            for p in self.preset_root.glob("*.json")
            if p.name not in {"index.json", "preset.schema.json", "project_config.schema.json"}
        )

    def get_preset_schema(self) -> dict:
        return self._preset_schema

    def get_project_schema(self) -> dict:
        return self._project_schema

    def list_presets(self) -> list[PresetSummary]:
        summaries: list[PresetSummary] = []
        self._preset_cache.clear()
        for path in self._iter_preset_files():
            preset_data = self._read_json(path)
            errors = list(self._preset_validator.iter_errors(preset_data))
            if errors:
                raise PresetCatalogError(
                    f"Preset schema validation failed for {path.name}: {errors[0].message}"
                )
            preset = Preset.model_validate(preset_data)
            self._preset_cache[preset.id] = preset
            summaries.append(
                PresetSummary(
                    id=preset.id,
                    name=preset.name,
                    family=preset.family,
                    kinematics=preset.kinematics,
                    build_volume=preset.build_volume,
                    supported_boards=preset.supported_boards,
                )
            )
        return sorted(summaries, key=lambda s: s.name.lower())

    def load_preset(self, preset_id: str) -> Preset:
        if not self._preset_cache:
            self.list_presets()
        preset = self._preset_cache.get(preset_id)
        if preset is None:
            raise PresetCatalogError(f"Unknown preset id '{preset_id}'.")
        return preset


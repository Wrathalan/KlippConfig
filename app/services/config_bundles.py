from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from app.domain.models import AddonProfile, BoardProfile
from app.services.paths import bundle_roots as default_bundle_roots


class BundleCatalogService:
    def __init__(self, bundle_roots: Iterable[Path] | None = None) -> None:
        roots = bundle_roots if bundle_roots is not None else default_bundle_roots()
        self.bundle_roots = [Path(root).expanduser() for root in roots]
        self._main_boards: dict[str, BoardProfile] | None = None
        self._toolhead_boards: dict[str, BoardProfile] | None = None
        self._addons: dict[str, AddonProfile] | None = None

    def reload(self) -> None:
        self._main_boards = self._load_board_profiles("boards")
        self._toolhead_boards = self._load_board_profiles("toolhead_boards")
        self._addons = self._load_addon_profiles()

    def load_main_board_profiles(self) -> dict[str, BoardProfile]:
        self._ensure_loaded()
        assert self._main_boards is not None
        return dict(self._main_boards)

    def load_toolhead_board_profiles(self) -> dict[str, BoardProfile]:
        self._ensure_loaded()
        assert self._toolhead_boards is not None
        return dict(self._toolhead_boards)

    def load_addon_profiles(self) -> dict[str, AddonProfile]:
        self._ensure_loaded()
        assert self._addons is not None
        return dict(self._addons)

    def _ensure_loaded(self) -> None:
        if self._main_boards is None or self._toolhead_boards is None or self._addons is None:
            self.reload()

    def _iter_bundle_files(self, subdir: str) -> list[Path]:
        files: list[Path] = []
        for root in self.bundle_roots:
            directory = root / subdir
            if not directory.exists() or not directory.is_dir():
                continue
            files.extend(sorted(directory.glob("*.json")))
        return files

    @staticmethod
    def _read_json_file(path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return data

    def _load_board_profiles(self, subdir: str) -> dict[str, BoardProfile]:
        profiles: dict[str, BoardProfile] = {}
        for path in self._iter_bundle_files(subdir):
            payload = self._read_json_file(path)
            if payload is None:
                continue
            board_id = str(payload.get("id") or path.stem).strip()
            if not board_id:
                continue
            body = dict(payload)
            body.pop("id", None)
            body.pop("type", None)
            try:
                profiles[board_id] = BoardProfile.model_validate(body)
            except ValidationError:
                continue
        return profiles

    def _load_addon_profiles(self) -> dict[str, AddonProfile]:
        profiles: dict[str, AddonProfile] = {}
        for path in self._iter_bundle_files("addons"):
            payload = self._read_json_file(path)
            if payload is None:
                continue

            addon_id = str(payload.get("id") or path.stem).strip()
            if not addon_id:
                continue
            template = str(payload.get("template") or f"addons/{addon_id}.cfg.j2").strip()
            if not template:
                template = f"addons/{addon_id}.cfg.j2"

            body = dict(payload)
            body["id"] = addon_id
            body["template"] = template
            body.pop("type", None)
            try:
                profiles[addon_id] = AddonProfile.model_validate(body)
            except ValidationError:
                continue
        return profiles

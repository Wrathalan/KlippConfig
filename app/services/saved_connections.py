from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class SavedConnectionService:
    def __init__(self, storage_path: Path | None = None) -> None:
        self.storage_path = storage_path or self._default_storage_path()

    @staticmethod
    def _default_storage_path() -> Path:
        if os.name == "nt":
            appdata = os.environ.get("APPDATA")
            base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
        else:
            xdg_config = os.environ.get("XDG_CONFIG_HOME")
            base = Path(xdg_config) if xdg_config else (Path.home() / ".config")
        return base / "KlippConfig" / "saved_connections.json"

    def _read_store(self) -> dict[str, dict[str, Any]]:
        if not self.storage_path.exists():
            return {}
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        profiles = raw.get("profiles") if isinstance(raw, dict) else None
        if not isinstance(profiles, dict):
            return {}

        cleaned: dict[str, dict[str, Any]] = {}
        for raw_name, payload in profiles.items():
            name = str(raw_name).strip()
            if not name or not isinstance(payload, dict):
                continue
            cleaned[name] = dict(payload)
        return cleaned

    def _write_store(self, profiles: dict[str, dict[str, Any]]) -> None:
        payload = {"profiles": profiles}
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def list_names(self) -> list[str]:
        return sorted(self._read_store().keys(), key=str.casefold)

    def load(self, name: str) -> dict[str, Any] | None:
        key = name.strip()
        if not key:
            return None
        profile = self._read_store().get(key)
        if profile is None:
            return None
        return dict(profile)

    def save(self, name: str, profile: dict[str, Any]) -> None:
        key = name.strip()
        if not key:
            raise ValueError("Connection name is required.")
        store = self._read_store()
        store[key] = dict(profile)
        self._write_store(store)

    def delete(self, name: str) -> bool:
        key = name.strip()
        if not key:
            return False
        store = self._read_store()
        if key not in store:
            return False
        del store[key]
        self._write_store(store)
        return True

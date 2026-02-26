from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SavedConnectionService:
    def __init__(self, storage_path: Path | None = None) -> None:
        self.storage_path = storage_path or self._default_storage_path()

    @staticmethod
    def _default_storage_path() -> Path:
        # Keep SSH connection configs out of app config and repository paths.
        return Path.home() / ".ssh" / "klippconfig" / "saved_connections.json"

    @staticmethod
    def _coerce_bool(raw: object, default: bool) -> bool:
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _normalize_preferences(raw: object) -> dict[str, object]:
        defaults: dict[str, object] = {
            "auto_connect_enabled": True,
            "default_connection_name": "",
        }
        if not isinstance(raw, dict):
            return defaults
        default_name = str(raw.get("default_connection_name") or "").strip()
        return {
            "auto_connect_enabled": SavedConnectionService._coerce_bool(
                raw.get("auto_connect_enabled"),
                True,
            ),
            "default_connection_name": default_name,
        }

    def _read_store(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "profiles": {},
            "preferences": self._normalize_preferences(None),
        }
        if not self.storage_path.exists():
            return payload
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return payload
        profiles = raw.get("profiles") if isinstance(raw, dict) else None
        if not isinstance(profiles, dict):
            return payload

        cleaned: dict[str, dict[str, Any]] = {}
        for raw_name, payload in profiles.items():
            name = str(raw_name).strip()
            if not name or not isinstance(payload, dict):
                continue
            cleaned[name] = dict(payload)
        return {
            "profiles": cleaned,
            "preferences": self._normalize_preferences(
                raw.get("preferences") if isinstance(raw, dict) else None
            ),
        }

    def _write_store(self, store: dict[str, Any]) -> None:
        profiles_raw = store.get("profiles")
        profiles = profiles_raw if isinstance(profiles_raw, dict) else {}
        preferences = self._normalize_preferences(store.get("preferences"))
        payload = {
            "profiles": profiles,
            "preferences": preferences,
        }
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def list_names(self) -> list[str]:
        store = self._read_store()
        profiles = store.get("profiles")
        if not isinstance(profiles, dict):
            return []
        return sorted(profiles.keys(), key=str.casefold)

    def load(self, name: str) -> dict[str, Any] | None:
        key = name.strip()
        if not key:
            return None
        store = self._read_store()
        profiles = store.get("profiles")
        if not isinstance(profiles, dict):
            return None
        profile = profiles.get(key)
        if profile is None:
            return None
        return dict(profile)

    def save(self, name: str, profile: dict[str, Any]) -> None:
        key = name.strip()
        if not key:
            raise ValueError("Connection name is required.")
        store = self._read_store()
        profiles_raw = store.get("profiles")
        profiles = profiles_raw if isinstance(profiles_raw, dict) else {}
        profiles[key] = dict(profile)
        store["profiles"] = profiles
        self._write_store(store)

    def delete(self, name: str) -> bool:
        key = name.strip()
        if not key:
            return False
        store = self._read_store()
        profiles_raw = store.get("profiles")
        profiles = profiles_raw if isinstance(profiles_raw, dict) else {}
        if key not in profiles:
            return False
        del profiles[key]
        store["profiles"] = profiles
        self._write_store(store)
        return True

    def get_auto_connect_enabled(self, default: bool = True) -> bool:
        store = self._read_store()
        preferences_raw = store.get("preferences")
        preferences = self._normalize_preferences(preferences_raw)
        return self._coerce_bool(preferences.get("auto_connect_enabled"), default)

    def set_auto_connect_enabled(self, enabled: bool) -> None:
        store = self._read_store()
        preferences = self._normalize_preferences(store.get("preferences"))
        preferences["auto_connect_enabled"] = bool(enabled)
        store["preferences"] = preferences
        self._write_store(store)

    def get_default_connection_name(self) -> str:
        store = self._read_store()
        preferences_raw = store.get("preferences")
        preferences = self._normalize_preferences(preferences_raw)
        return str(preferences.get("default_connection_name") or "").strip()

    def set_default_connection_name(self, name: str) -> None:
        store = self._read_store()
        preferences = self._normalize_preferences(store.get("preferences"))
        preferences["default_connection_name"] = name.strip()
        store["preferences"] = preferences
        self._write_store(store)

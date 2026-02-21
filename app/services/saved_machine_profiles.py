from __future__ import annotations

import json
from pathlib import Path

from app.domain.models import ImportedMachineProfile
from app.services.paths import user_data_dir


class SavedMachineProfileService:
    def __init__(self, storage_path: Path | None = None) -> None:
        self.storage_path = storage_path or (user_data_dir() / "saved_machine_profiles.json")

    def _read_store(self) -> dict[str, dict]:
        if not self.storage_path.exists():
            return {}
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        profiles = raw.get("profiles") if isinstance(raw, dict) else None
        if not isinstance(profiles, dict):
            return {}

        cleaned: dict[str, dict] = {}
        for raw_name, payload in profiles.items():
            name = str(raw_name).strip()
            if not name or not isinstance(payload, dict):
                continue
            cleaned[name] = payload
        return cleaned

    def _write_store(self, profiles: dict[str, dict]) -> None:
        payload = {"profiles": profiles}
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def list_names(self) -> list[str]:
        return sorted(self._read_store().keys(), key=str.casefold)

    def save(self, name: str, profile: ImportedMachineProfile) -> None:
        key = name.strip()
        if not key:
            raise ValueError("Profile name is required.")
        store = self._read_store()
        store[key] = profile.model_dump(mode="json")
        self._write_store(store)

    def load(self, name: str) -> ImportedMachineProfile | None:
        key = name.strip()
        if not key:
            return None
        payload = self._read_store().get(key)
        if payload is None:
            return None
        try:
            return ImportedMachineProfile.model_validate(payload)
        except Exception:  # noqa: BLE001
            return None

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

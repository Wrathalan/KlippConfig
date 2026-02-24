from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.paths import user_data_dir


class ActionLogService:
    """Append-only structured action log for key operator workflows."""

    def __init__(self, log_path: Path | None = None) -> None:
        if log_path is None:
            log_path = user_data_dir() / "logs" / "actions.log"
        self.log_path = log_path.expanduser()

    def log_event(self, action: str, **fields: Any) -> None:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
        }
        payload.update(fields)
        self._append_json_line(payload)

    def _append_json_line(self, payload: dict[str, Any]) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
        except OSError:
            # Logging must never break user workflows.
            return

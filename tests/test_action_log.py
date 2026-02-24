from __future__ import annotations

import json

from app.services.action_log import ActionLogService


def test_action_log_service_writes_json_lines(tmp_path) -> None:
    log_path = tmp_path / "logs" / "actions.log"
    service = ActionLogService(log_path=log_path)

    service.log_event("connect", phase="start", host="printer.local")
    service.log_event("validate", blocking=0, warnings=1)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["action"] == "connect"
    assert first["phase"] == "start"
    assert first["host"] == "printer.local"
    assert "timestamp" in first
    assert second["action"] == "validate"
    assert second["warnings"] == 1

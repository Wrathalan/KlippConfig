from __future__ import annotations

import json
from pathlib import Path

from app.domain.models import ProjectConfig


class ProjectStoreService:
    def save(self, path: str, project: ProjectConfig) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(project.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def load(self, path: str) -> ProjectConfig:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return ProjectConfig.model_validate(data)


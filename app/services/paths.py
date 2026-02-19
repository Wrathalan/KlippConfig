from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "app"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def presets_dir() -> Path:
    return app_root() / "presets"


def schemas_dir() -> Path:
    return presets_dir() / "schemas"


def templates_dir() -> Path:
    return app_root() / "templates"


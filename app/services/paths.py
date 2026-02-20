from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "app"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def _asset_candidates() -> list[Path]:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        candidates.extend([meipass / "app" / "assets", meipass / "assets"])
    # Local dev paths:
    # - repo_root/assets (current layout)
    # - app/assets (older/alternate layout)
    candidates.extend(
        [
            Path(__file__).resolve().parents[2] / "assets",
            Path(__file__).resolve().parents[1] / "assets",
        ]
    )
    return candidates


def _resolve_asset(name: str) -> Path:
    for directory in _asset_candidates():
        candidate = directory / name
        if candidate.exists():
            return candidate
    # Fallback keeps previous behavior shape while making failures explicit to caller.
    return app_root() / "assets" / name


def presets_dir() -> Path:
    return app_root() / "presets"


def schemas_dir() -> Path:
    return presets_dir() / "schemas"


def templates_dir() -> Path:
    return app_root() / "templates"


def icon_path() -> Path:
    return _resolve_asset("icon.ico")


def creator_icon_path() -> Path:
    return _resolve_asset("creator.ico")

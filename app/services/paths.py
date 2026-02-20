from __future__ import annotations

import os
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


def bundles_dir() -> Path:
    return app_root() / "bundles"


def user_data_dir() -> Path:
    appdata = os.getenv("APPDATA")
    if appdata:
        return Path(appdata) / "KlippConfig"
    return Path.home() / ".klippconfig"


def user_bundles_dir() -> Path:
    return user_data_dir() / "bundles"


def bundle_roots() -> list[Path]:
    roots: list[Path] = []
    configured = (os.getenv("KLIPPCONFIG_BUNDLE_DIRS") or "").strip()
    if configured:
        for item in configured.split(os.pathsep):
            candidate = Path(item.strip()).expanduser()
            if candidate:
                roots.append(candidate)
    roots.append(bundles_dir())
    roots.append(user_bundles_dir())
    # Deduplicate while preserving order.
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


def bundle_template_dirs() -> list[Path]:
    return [root / "templates" for root in bundle_roots()]


def icon_path() -> Path:
    return _resolve_asset("icon.ico")


def creator_icon_path() -> Path:
    return _resolve_asset("creator.ico")

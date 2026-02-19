from __future__ import annotations

from typing import Literal
from weakref import WeakKeyDictionary

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


UIScaleMode = Literal["auto", "85", "90", "100", "110", "125", "150"]

DEFAULT_MODE: UIScaleMode = "auto"
SETTINGS_KEY = "ui/scale_mode"
SCALE_MAP: dict[UIScaleMode, float] = {
    "auto": 1.0,
    "85": 0.85,
    "90": 0.90,
    "100": 1.0,
    "110": 1.10,
    "125": 1.25,
    "150": 1.50,
}


class UIScalingService:
    _baseline_point_sizes: "WeakKeyDictionary[QApplication, float]" = WeakKeyDictionary()

    def __init__(self, settings: QSettings | None = None) -> None:
        self.settings = settings or QSettings("KlippConfig", "KlippConfig")

    @classmethod
    def _normalize_mode(cls, raw: object) -> UIScaleMode | None:
        if raw is None:
            return None
        text = str(raw).strip().lower()
        if not text:
            return None
        if text.endswith("%"):
            text = text[:-1]

        if text in SCALE_MAP:
            return text  # type: ignore[return-value]

        numeric_aliases = {
            "1": "100",
            "1.0": "100",
            "0.85": "85",
            "0.9": "90",
            "1.1": "110",
            "1.25": "125",
            "1.5": "150",
        }
        aliased = numeric_aliases.get(text)
        if aliased is None:
            return None
        return aliased  # type: ignore[return-value]

    def load_mode(self) -> UIScaleMode:
        stored = self.settings.value(SETTINGS_KEY, DEFAULT_MODE, type=str)
        return self._normalize_mode(stored) or DEFAULT_MODE

    def save_mode(self, mode: UIScaleMode) -> None:
        normalized = self._normalize_mode(mode) or DEFAULT_MODE
        self.settings.setValue(SETTINGS_KEY, normalized)
        self.settings.sync()

    def resolve_mode(
        self,
        cli: str | None = None,
        env: str | None = None,
        saved: UIScaleMode | None = None,
    ) -> UIScaleMode:
        for candidate in (cli, env, saved, DEFAULT_MODE):
            normalized = self._normalize_mode(candidate)
            if normalized is not None:
                return normalized
        return DEFAULT_MODE

    @staticmethod
    def _extract_baseline_point_size(font: QFont) -> float:
        size = font.pointSizeF()
        if size <= 0:
            size = float(font.pointSize())
        if size <= 0:
            size = 9.0
        return size

    def apply(self, app: QApplication, mode: UIScaleMode) -> None:
        normalized = self._normalize_mode(mode) or DEFAULT_MODE
        factor = SCALE_MAP[normalized]

        base_size = self._baseline_point_sizes.get(app)
        if base_size is None:
            base_size = self._extract_baseline_point_size(app.font())
            self._baseline_point_sizes[app] = base_size

        font = QFont(app.font())
        font.setPointSizeF(max(1.0, base_size * factor))
        app.setFont(font)

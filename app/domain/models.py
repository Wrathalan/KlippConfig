from __future__ import annotations

from collections import OrderedDict
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


Severity = Literal["blocking", "warning"]
AddonName = str


class BuildVolume(BaseModel):
    x: int = Field(gt=0)
    y: int = Field(gt=0)
    z: int = Field(gt=0)


class MotionDefaults(BaseModel):
    max_velocity: int = Field(gt=0)
    max_accel: int = Field(gt=0)
    square_corner_velocity: float = Field(gt=0)


class FeatureFlags(BaseModel):
    probe_optional: bool
    input_shaper_optional: bool
    macros_supported: bool


class TemplateRefs(BaseModel):
    printer: str
    mcu: str
    motion: str
    thermal: str


class BoardProfile(BaseModel):
    label: str
    mcu: str
    serial_hint: str
    pins: dict[str, str]
    layout: dict[str, list[str]] = Field(default_factory=dict)


class AddonProfile(BaseModel):
    id: str
    label: str
    template: str
    description: str = ""
    multi_material: bool = False
    recommends_toolhead: bool = False
    supported_families: list[str] = Field(default_factory=lambda: ["voron"])
    supported_presets: list[str] = Field(default_factory=list)


class Preset(BaseModel):
    id: str
    name: str
    family: Literal["voron"]
    kinematics: Literal["corexy", "cartesian"]
    build_volume: BuildVolume
    supported_boards: list[str]
    defaults: MotionDefaults
    feature_flags: FeatureFlags
    templates: TemplateRefs
    board_profiles: dict[str, BoardProfile] = Field(default_factory=dict)
    supported_toolhead_boards: list[str] = Field(default_factory=list)
    supported_addons: list[AddonName] = Field(default_factory=list)
    recommended_probe_types: list[str] = Field(default_factory=list)
    notes: str = ""
    version: int = 1

    @model_validator(mode="after")
    def _supported_boards_exist(self) -> "Preset":
        if not self.board_profiles:
            return self
        missing = [board for board in self.supported_boards if board not in self.board_profiles]
        if missing:
            raise ValueError(
                f"Preset '{self.id}' references boards with no board_profiles: {', '.join(missing)}"
            )
        return self


class ProbeConfig(BaseModel):
    enabled: bool = False
    type: str | None = None


class ThermistorConfig(BaseModel):
    hotend: str = "EPCOS 100K B57560G104F"
    bed: str = "EPCOS 100K B57560G104F"


class Dimensions(BaseModel):
    x: int = Field(gt=0)
    y: int = Field(gt=0)
    z: int = Field(gt=0)


class ToolheadConfig(BaseModel):
    enabled: bool = False
    board: str | None = None
    canbus_uuid: str | None = None


class LEDConfig(BaseModel):
    enabled: bool = False
    pin: str | None = "PA8"
    chain_count: int = Field(default=1, ge=1, le=256)
    color_order: Literal["RGB", "GRB", "BRG", "BGR"] = "GRB"
    initial_red: float = Field(default=0.0, ge=0.0, le=1.0)
    initial_green: float = Field(default=0.0, ge=0.0, le=1.0)
    initial_blue: float = Field(default=0.0, ge=0.0, le=1.0)


class ProjectConfig(BaseModel):
    preset_id: str
    board: str
    dimensions: Dimensions
    probe: ProbeConfig = Field(default_factory=ProbeConfig)
    thermistors: ThermistorConfig = Field(default_factory=ThermistorConfig)
    motion_profile: Literal["safe"] = "safe"
    macro_packs: list[Literal["core_maintenance", "qgl_helpers", "filament_ops"]] = Field(
        default_factory=list
    )
    addons: list[AddonName] = Field(default_factory=list)
    toolhead: ToolheadConfig = Field(default_factory=ToolheadConfig)
    leds: LEDConfig = Field(default_factory=LEDConfig)
    advanced_overrides: dict[str, Any] = Field(default_factory=dict)


class PresetSummary(BaseModel):
    id: str
    name: str
    family: Literal["voron"]
    kinematics: Literal["corexy", "cartesian"]
    build_volume: BuildVolume
    supported_boards: list[str]


class ValidationFinding(BaseModel):
    severity: Severity
    code: str
    message: str
    field: str | None = None


class ValidationReport(BaseModel):
    findings: list[ValidationFinding] = Field(default_factory=list)

    def add(
        self, severity: Severity, code: str, message: str, field: str | None = None
    ) -> None:
        self.findings.append(
            ValidationFinding(severity=severity, code=code, message=message, field=field)
        )

    @property
    def has_blocking(self) -> bool:
        return any(f.severity == "blocking" for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.severity == "warning" for f in self.findings)


class RenderedPack(BaseModel):
    files: OrderedDict[str, str]
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConfigFileRole(str, Enum):
    ROOT = "root"
    INCLUDE_FRAGMENT = "include_fragment"
    MACRO_PACK = "macro_pack"
    MCU_MAP = "mcu_map"


class ImportSuggestion(BaseModel):
    field: str
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    source_file: str
    auto_apply: bool = False


class ImportedMachineProfile(BaseModel):
    name: str
    root_file: str
    source_kind: Literal["zip", "folder"]
    detected: dict[str, Any] = Field(default_factory=dict)
    suggestions: list[ImportSuggestion] = Field(default_factory=list)
    include_graph: dict[str, list[str]] = Field(default_factory=dict)
    analysis_warnings: list[str] = Field(default_factory=list)

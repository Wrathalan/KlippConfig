from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable


@dataclass(frozen=True)
class ConnectionState:
    connected: bool = False
    host: str = ""
    target_printer: str = ""
    profile_name: str = ""
    last_updated_utc: str = ""


@dataclass(frozen=True)
class ActiveFileState:
    path: str = ""
    source: str = ""  # generated|remote|local|imported
    dirty: bool = False
    last_updated_utc: str = ""


@dataclass(frozen=True)
class ValidationState:
    blocking: int = 0
    warnings: int = 0
    source_label: str = ""
    last_updated_utc: str = ""


@dataclass(frozen=True)
class DeployState:
    upload_in_progress: bool = False
    last_upload_status: str = ""
    last_restart_status: str = ""
    last_updated_utc: str = ""


@dataclass(frozen=True)
class UIState:
    active_route: str = "home"
    right_panel_mode: str = "context"  # context|validation|logs
    left_nav_visible: bool = True
    legacy_visible: bool = True
    files_ui_variant: str = "classic"  # classic|material_v1
    last_updated_utc: str = ""


@dataclass(frozen=True)
class AppState:
    connection: ConnectionState = field(default_factory=ConnectionState)
    active_file: ActiveFileState = field(default_factory=ActiveFileState)
    validation: ValidationState = field(default_factory=ValidationState)
    deploy: DeployState = field(default_factory=DeployState)
    ui: UIState = field(default_factory=UIState)


Listener = Callable[[AppState], None]


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class AppStateStore:
    """Centralized in-memory app state with coarse-grained update helpers."""

    def __init__(self) -> None:
        self._state = AppState()
        self._listeners: list[Listener] = []

    def snapshot(self) -> AppState:
        return self._state

    def subscribe(self, listener: Listener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def update_connection(
        self,
        *,
        connected: bool,
        host: str = "",
        target_printer: str = "",
        profile_name: str = "",
    ) -> None:
        connection = ConnectionState(
            connected=connected,
            host=host,
            target_printer=target_printer,
            profile_name=profile_name,
            last_updated_utc=_now_utc(),
        )
        self._publish(replace(self._state, connection=connection))

    def update_active_file(self, *, path: str, source: str, dirty: bool) -> None:
        active_file = ActiveFileState(
            path=path,
            source=source,
            dirty=dirty,
            last_updated_utc=_now_utc(),
        )
        self._publish(replace(self._state, active_file=active_file))

    def update_validation(self, *, blocking: int, warnings: int, source_label: str) -> None:
        validation = ValidationState(
            blocking=max(0, int(blocking)),
            warnings=max(0, int(warnings)),
            source_label=source_label,
            last_updated_utc=_now_utc(),
        )
        self._publish(replace(self._state, validation=validation))

    def update_deploy(
        self,
        *,
        upload_in_progress: bool | None = None,
        last_upload_status: str | None = None,
        last_restart_status: str | None = None,
    ) -> None:
        current = self._state.deploy
        deploy = DeployState(
            upload_in_progress=(
                current.upload_in_progress
                if upload_in_progress is None
                else bool(upload_in_progress)
            ),
            last_upload_status=(
                current.last_upload_status
                if last_upload_status is None
                else str(last_upload_status)
            ),
            last_restart_status=(
                current.last_restart_status
                if last_restart_status is None
                else str(last_restart_status)
            ),
            last_updated_utc=_now_utc(),
        )
        self._publish(replace(self._state, deploy=deploy))

    def update_ui(
        self,
        *,
        active_route: str | None = None,
        right_panel_mode: str | None = None,
        left_nav_visible: bool | None = None,
        legacy_visible: bool | None = None,
        files_ui_variant: str | None = None,
    ) -> None:
        current = self._state.ui
        ui = UIState(
            active_route=current.active_route if active_route is None else str(active_route),
            right_panel_mode=(
                current.right_panel_mode
                if right_panel_mode is None
                else str(right_panel_mode)
            ),
            left_nav_visible=(
                current.left_nav_visible
                if left_nav_visible is None
                else bool(left_nav_visible)
            ),
            legacy_visible=(
                current.legacy_visible if legacy_visible is None else bool(legacy_visible)
            ),
            files_ui_variant=(
                current.files_ui_variant
                if files_ui_variant is None
                else str(files_ui_variant)
            ),
            last_updated_utc=_now_utc(),
        )
        self._publish(replace(self._state, ui=ui))

    def _publish(self, next_state: AppState) -> None:
        self._state = next_state
        for listener in list(self._listeners):
            listener(self._state)

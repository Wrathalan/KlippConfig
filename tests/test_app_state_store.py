from __future__ import annotations

from app.ui.app_state import AppStateStore


def test_app_state_store_updates_core_slices() -> None:
    store = AppStateStore()

    store.update_connection(
        connected=True,
        host="192.168.1.20",
        target_printer="Voron",
        profile_name="Lab",
    )
    store.update_active_file(path="printer.cfg", source="remote", dirty=False)
    store.update_validation(blocking=1, warnings=2, source_label="printer.cfg")
    store.update_deploy(upload_in_progress=True, last_upload_status="starting")
    store.update_ui(active_route="connect", right_panel_mode="logs", files_ui_variant="material_v1")

    state = store.snapshot()
    assert state.connection.connected is True
    assert state.connection.host == "192.168.1.20"
    assert state.active_file.path == "printer.cfg"
    assert state.validation.blocking == 1
    assert state.validation.warnings == 2
    assert state.deploy.upload_in_progress is True
    assert state.deploy.last_upload_status == "starting"
    assert state.ui.active_route == "connect"
    assert state.ui.right_panel_mode == "logs"
    assert state.ui.files_ui_variant == "material_v1"


def test_app_state_store_notifies_subscribers() -> None:
    store = AppStateStore()
    seen: list[str] = []

    def listener(state) -> None:  # noqa: ANN001
        seen.append(state.ui.active_route)

    store.subscribe(listener)
    store.update_ui(active_route="home")
    store.update_ui(active_route="files")
    store.unsubscribe(listener)
    store.update_ui(active_route="files")

    assert seen == ["home", "files"]

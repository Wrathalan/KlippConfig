from __future__ import annotations

from pathlib import Path

from app.services.saved_connections import SavedConnectionService


def test_saved_connections_save_load_and_delete(tmp_path) -> None:
    service = SavedConnectionService(storage_path=tmp_path / "saved_connections.json")
    profile = {
        "host": "printer.local",
        "port": 22,
        "username": "pi",
        "password": "supersecret",
        "key_path": "C:/Users/test/.ssh/id_ed25519",
        "remote_dir": "~/printer_data/config",
        "remote_file": "~/printer_data/config/printer.cfg",
    }

    service.save("V2.4", profile)

    assert service.list_names() == ["V2.4"]
    loaded = service.load("V2.4")
    assert loaded is not None
    assert loaded["host"] == "printer.local"
    assert loaded["username"] == "pi"
    assert loaded["password"] == "supersecret"

    assert service.delete("V2.4") is True
    assert service.list_names() == []
    assert service.load("V2.4") is None


def test_saved_connections_preferences_roundtrip(tmp_path) -> None:
    service = SavedConnectionService(storage_path=tmp_path / "saved_connections.json")
    assert service.get_auto_connect_enabled(default=True) is True
    assert service.get_default_connection_name() == ""

    service.set_auto_connect_enabled(False)
    service.set_default_connection_name("Lab Printer")

    assert service.get_auto_connect_enabled(default=True) is False
    assert service.get_default_connection_name() == "Lab Printer"


def test_default_storage_path_uses_ssh_directory() -> None:
    service = SavedConnectionService()
    expected = Path.home() / ".ssh" / "klippconfig" / "saved_connections.json"
    assert service.storage_path == expected

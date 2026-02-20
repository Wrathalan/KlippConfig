from __future__ import annotations

from app.services.saved_connections import SavedConnectionService


def test_saved_connections_save_load_and_delete(tmp_path) -> None:
    service = SavedConnectionService(storage_path=tmp_path / "saved_connections.json")
    profile = {
        "host": "printer.local",
        "port": 22,
        "username": "pi",
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

    assert service.delete("V2.4") is True
    assert service.list_names() == []
    assert service.load("V2.4") is None

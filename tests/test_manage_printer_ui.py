from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

import app.ui.main_window as main_window_module
from app.ui.main_window import MainWindow


class FakeManageSSHService:
    def __init__(self) -> None:
        self.saved: tuple[str, str] | None = None
        self.restored: tuple[str, str, bool] | None = None
        self.downloaded: tuple[str, str] | None = None
        self.backup_count = 0
        self.directories = {
            "/home/pi/printer_data/config": [
                {
                    "name": "extras",
                    "path": "/home/pi/printer_data/config/extras",
                    "type": "dir",
                },
                {
                    "name": "macros.cfg",
                    "path": "/home/pi/printer_data/config/macros.cfg",
                    "type": "file",
                },
                {
                    "name": "printer.cfg",
                    "path": "/home/pi/printer_data/config/printer.cfg",
                    "type": "file",
                },
            ],
            "/home/pi/printer_data/config/extras": [
                {
                    "name": "test.cfg",
                    "path": "/home/pi/printer_data/config/extras/test.cfg",
                    "type": "file",
                }
            ],
        }

    @staticmethod
    def _expand(remote_dir: str) -> str:
        value = remote_dir.strip()
        if value.startswith("~/"):
            return f"/home/pi/{value[2:]}"
        if value == "~":
            return "/home/pi"
        return value

    def list_directory(self, **kwargs):
        directory = self._expand(kwargs["remote_dir"])
        return {"directory": directory, "entries": list(self.directories.get(directory, []))}

    def fetch_file(self, **kwargs):
        return f"# contents for {kwargs['remote_path']}\n"

    def write_file(self, **kwargs):
        self.saved = (kwargs["remote_path"], kwargs["content"])
        return kwargs["remote_path"]

    def create_backup(self, **_kwargs):
        self.backup_count += 1
        return f"/home/pi/klippconfig_backups/backup-20260101-00000{self.backup_count}"

    def list_backups(self, **_kwargs):
        return [
            "/home/pi/klippconfig_backups/backup-20260101-000001",
            "/home/pi/klippconfig_backups/backup-20260101-000002",
        ]

    def restore_backup(self, **kwargs):
        self.restored = (
            kwargs["remote_dir"],
            kwargs["backup_path"],
            kwargs["clear_before_restore"],
        )

    def download_backup(self, **kwargs):
        self.downloaded = (kwargs["backup_path"], kwargs["local_destination"])
        return kwargs["local_destination"]


def _walk_tree_items(tree_widget):
    stack = [tree_widget.topLevelItem(index) for index in range(tree_widget.topLevelItemCount())]
    while stack:
        item = stack.pop()
        yield item
        for child_index in range(item.childCount() - 1, -1, -1):
            stack.append(item.child(child_index))


def _find_tree_item_by_path(window: MainWindow, remote_path: str):
    role = window._manage_tree_path_role()
    for item in _walk_tree_items(window.manage_file_tree):
        value = item.data(0, role)
        if str(value or "") == remote_path:
            return item
    return None


def test_manage_printer_tab_file_edit_and_backup_flow(qtbot, monkeypatch, tmp_path) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    fake_service = FakeManageSSHService()
    window.ssh_service = fake_service

    window.ssh_host_edit.setText("192.168.1.20")
    window.ssh_username_edit.setText("pi")
    window._use_ssh_host_for_manage()

    window._manage_refresh_files()
    assert window.manage_file_tree.topLevelItemCount() == 1

    printer_item = _find_tree_item_by_path(window, "/home/pi/printer_data/config/printer.cfg")
    assert printer_item is not None
    window.manage_file_tree.setCurrentItem(printer_item)
    window._manage_open_selected_file()
    assert "printer.cfg" in window.manage_current_file_label.text()

    window.manage_file_editor.setPlainText("updated file contents\n")
    window._manage_save_current_file()
    assert fake_service.saved is not None
    assert fake_service.saved[1] == "updated file contents\n"

    window._manage_create_backup()
    window._manage_refresh_backups()
    assert window.manage_backup_combo.count() == 2

    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    window.manage_backup_combo.setCurrentIndex(0)
    window._manage_restore_selected_backup()
    assert fake_service.restored is not None
    assert fake_service.restored[1].endswith("backup-20260101-000001")

    monkeypatch.setattr(window, "_desktop_backup_download_root", lambda: tmp_path)
    window._manage_download_selected_backup()
    assert fake_service.downloaded is not None
    assert fake_service.downloaded[0].endswith("backup-20260101-000001")
    assert str(tmp_path) in fake_service.downloaded[1]


def test_manage_printer_can_explore_directories(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    fake_service = FakeManageSSHService()
    window.ssh_service = fake_service

    window.ssh_host_edit.setText("192.168.1.20")
    window.ssh_username_edit.setText("pi")
    window._use_ssh_host_for_manage()

    window._manage_refresh_files()
    assert "Tree root: /home/pi/printer_data/config" in window.manage_current_dir_label.text()

    extras_item = _find_tree_item_by_path(window, "/home/pi/printer_data/config/extras")
    assert extras_item is not None
    window.manage_file_tree.setCurrentItem(extras_item)
    window._manage_open_selected_file()
    assert "Tree root: /home/pi/printer_data/config/extras" in window.manage_current_dir_label.text()

    extras_file = _find_tree_item_by_path(window, "/home/pi/printer_data/config/extras/test.cfg")
    assert extras_file is not None
    window._manage_browse_up_directory()
    assert "Tree root: /home/pi/printer_data/config" in window.manage_current_dir_label.text()
    root_file_item = _find_tree_item_by_path(window, "/home/pi/printer_data/config/printer.cfg")
    assert root_file_item is not None


def test_manage_control_url_resolution(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    window.manage_host_edit.setText("192.168.1.20")
    assert window._resolve_manage_control_url() == "http://192.168.1.20"

    window.manage_control_url_edit.setText("printer.local/mainsail")
    assert window._resolve_manage_control_url() == "http://printer.local/mainsail"


def test_manage_open_control_window_loads_embedded_view(qtbot, monkeypatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    loaded_urls: list[str] = []
    opened_urls: list[str] = []

    class FakeEmbeddedView:
        def setUrl(self, url) -> None:  # noqa: ANN001
            loaded_urls.append(url.toString())

    monkeypatch.setattr(window, "printers_embedded_control_view", FakeEmbeddedView())
    monkeypatch.setattr(
        main_window_module.QDesktopServices,
        "openUrl",
        lambda url: opened_urls.append(url.toString()) or True,
    )

    window.manage_host_edit.setText("printer.local")
    window._manage_open_control_window()

    assert loaded_urls == ["http://printer.local"]
    assert opened_urls == []
    assert window.tabs.currentWidget() is window.printers_tab
    assert window.app_state_store.snapshot().ui.active_route == "printers"


def test_manage_open_control_window_falls_back_to_browser(qtbot, monkeypatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)

    opened_urls: list[str] = []

    def fake_open_url(url) -> bool:
        opened_urls.append(url.toString())
        return True

    monkeypatch.setattr(window, "printers_embedded_control_view", None)
    monkeypatch.setattr(main_window_module.QDesktopServices, "openUrl", fake_open_url)

    window.manage_host_edit.setText("printer.local")
    window._manage_open_control_window()

    assert opened_urls == ["http://printer.local"]

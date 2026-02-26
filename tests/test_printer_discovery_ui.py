from __future__ import annotations

from app.services.printer_discovery import DiscoveredPrinter
from app.ui.main_window import MainWindow


def test_scan_populates_results_and_sets_host(qtbot, monkeypatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.preset_combo.count() > 0)
    window._open_printer_discovery()
    assert window.printer_discovery_window is not None
    assert window.printer_discovery_window.isVisible()

    fake_results = [
        DiscoveredPrinter(
            host="192.168.1.20",
            moonraker=True,
            ssh=True,
            moonraker_status="http 200",
            ssh_banner="SSH-2.0-dropbear",
        )
    ]
    monkeypatch.setattr(
        window.discovery_service,
        "scan",
        lambda _cidr, timeout, max_hosts: fake_results,
    )

    window.scan_cidr_edit.setText("192.168.1.0/24")
    window._scan_for_printers()

    assert window.discovery_results_table.rowCount() == 1
    assert window.discovery_results_table.item(0, 0).text() == "192.168.1.20"

    window.discovery_results_table.selectRow(0)
    window._use_selected_discovery_host()
    assert window.ssh_host_edit.text() == "192.168.1.20"
    assert window.manage_host_edit.text() == "192.168.1.20"

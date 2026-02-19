from __future__ import annotations

import pytest

from app.services.printer_discovery import (
    DiscoveredPrinter,
    PrinterDiscoveryError,
    PrinterDiscoveryService,
)


def test_suggest_scan_cidrs_from_local_addresses(monkeypatch) -> None:
    service = PrinterDiscoveryService()
    monkeypatch.setattr(
        service,
        "_discover_local_ipv4_addresses",
        lambda: {"192.168.50.10", "10.0.0.4"},
    )

    cidrs = service.suggest_scan_cidrs()
    assert "10.0.0.0/24" in cidrs
    assert "192.168.50.0/24" in cidrs


def test_scan_invalid_cidr_raises() -> None:
    service = PrinterDiscoveryService()
    with pytest.raises(PrinterDiscoveryError):
        service.scan("not-a-cidr")


def test_scan_merges_host_results_and_honors_max_hosts(monkeypatch) -> None:
    service = PrinterDiscoveryService()
    calls: list[str] = []

    def fake_probe(host: str, _timeout: float) -> list[DiscoveredPrinter]:
        calls.append(host)
        if host.endswith(".1"):
            return [
                DiscoveredPrinter(host=host, moonraker=True, moonraker_status="http 200"),
                DiscoveredPrinter(host=host, ssh=True, ssh_banner="SSH-2.0-dropbear"),
            ]
        if host.endswith(".2"):
            return [DiscoveredPrinter(host=host, ssh=True, ssh_banner="port 22 open")]
        return []

    monkeypatch.setattr(service, "_probe_host", fake_probe)

    results = service.scan("192.168.1.0/29", max_hosts=2, workers=2)
    assert len(calls) == 2
    assert len(results) == 2

    first = next(item for item in results if item.host.endswith(".1"))
    assert first.moonraker is True
    assert first.ssh is True
    assert first.moonraker_status == "http 200"
    assert first.ssh_banner == "SSH-2.0-dropbear"

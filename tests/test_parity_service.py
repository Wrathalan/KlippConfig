from __future__ import annotations

from collections import OrderedDict

from app.domain.models import RenderedPack
from app.services.parity import ParityService


def test_parity_ignores_serial_and_canbus_uuid_differences() -> None:
    service = ParityService()
    generated = RenderedPack(
        files=OrderedDict(
            {
                "config/printer.cfg": "[include nhk.cfg]\n[printer]\nkinematics: corexy\n",
                "config/nhk.cfg": (
                    "[mcu nhk]\n"
                    "serial: /dev/serial/by-id/usb-OTHER\n"
                    "canbus_uuid: deadbeef\n"
                ),
            }
        )
    )
    imported = {
        "config/printer.cfg": "[include nhk.cfg]\n[printer]\nkinematics: corexy\n",
        "config/nhk.cfg": (
            "[mcu nhk]\n"
            "serial: /dev/serial/by-id/usb-ORIGINAL\n"
            "canbus_uuid: abcdef12\n"
        ),
    }

    report = service.compare(generated, imported, imported_root_file="config/printer.cfg")
    assert not report.has_blocking


def test_parity_reports_blocking_when_required_key_differs() -> None:
    service = ParityService()
    generated = RenderedPack(
        files=OrderedDict(
            {
                "config/printer.cfg": (
                    "[printer]\n"
                    "kinematics: corexy\n"
                    "max_velocity: 300\n"
                ),
            }
        )
    )
    imported = {
        "config/printer.cfg": (
            "[printer]\n"
            "kinematics: corexy\n"
            "max_velocity: 400\n"
        )
    }

    report = service.compare(generated, imported, imported_root_file="config/printer.cfg")
    assert report.has_blocking
    assert any(f.code == "PARITY_VALUE_DIFF" for f in report.findings)

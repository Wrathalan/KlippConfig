from __future__ import annotations

from app.domain.models import ImportSuggestion, ImportedMachineProfile
from app.services.saved_machine_profiles import SavedMachineProfileService


def _sample_profile() -> ImportedMachineProfile:
    return ImportedMachineProfile(
        name="Fixture Printer",
        root_file="config/printer.cfg",
        source_kind="folder",
        detected={
            "preset_id": "voron_2_4_350",
            "file_map": {"config/printer.cfg": "[printer]\nkinematics: corexy\n"},
        },
        suggestions=[
            ImportSuggestion(
                field="preset_id",
                value="voron_2_4_350",
                confidence=0.95,
                reason="Detected quad gantry level",
                source_file="config/printer.cfg",
                auto_apply=True,
            )
        ],
        include_graph={"config/printer.cfg": []},
        analysis_warnings=[],
    )


def test_saved_machine_profiles_round_trip(tmp_path) -> None:
    service = SavedMachineProfileService(storage_path=tmp_path / "saved_machine_profiles.json")
    profile = _sample_profile()

    service.save("My Imported V2", profile)

    assert service.list_names() == ["My Imported V2"]
    loaded = service.load("My Imported V2")
    assert loaded is not None
    assert loaded.name == "Fixture Printer"
    assert loaded.root_file == "config/printer.cfg"
    assert loaded.detected["preset_id"] == "voron_2_4_350"

    assert service.delete("My Imported V2") is True
    assert service.list_names() == []
    assert service.load("My Imported V2") is None

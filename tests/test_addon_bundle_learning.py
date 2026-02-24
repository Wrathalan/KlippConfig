from __future__ import annotations

from pathlib import Path

from app.services.addon_bundle_learning import AddonBundleLearningService
from app.services.existing_machine_import import ExistingMachineImportService


def _fixture_root() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "existing_machine_sample"


def test_learn_from_import_writes_addon_bundle_files(tmp_path) -> None:
    importer = ExistingMachineImportService()
    profile = importer.import_folder(str(_fixture_root()))
    file_map = importer.last_import_files
    service = AddonBundleLearningService(bundle_root=tmp_path / "bundles")

    created = service.learn_from_import(profile, file_map)

    assert created
    addon_jsons = {path.name for path in created if path.suffix == ".json"}
    assert "kamp.json" in addon_jsons
    assert "stealthburner_leds.json" in addon_jsons
    assert "timelapse.json" in addon_jsons

    kamp_json = (tmp_path / "bundles" / "addons" / "kamp.json").read_text(encoding="utf-8")
    assert "\"package_templates\"" in kamp_json
    assert "\"include_files\"" in kamp_json

    kamp_template = tmp_path / "bundles" / "templates" / "addons" / "learned" / "kamp" / "KAMP_Settings.cfg.j2"
    assert kamp_template.exists()

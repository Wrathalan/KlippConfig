from app.services.preset_catalog import PresetCatalogService


def test_catalog_loads_voron_presets() -> None:
    service = PresetCatalogService()
    summaries = service.list_presets()

    assert len(summaries) == 8
    assert all(summary.id.startswith("voron_") for summary in summaries)
    assert all(summary.family == "voron" for summary in summaries)

from zipfile import ZipFile

from app.domain.models import RenderedPack
from app.services.exporter import ExportService


def test_export_folder_and_zip(tmp_path) -> None:
    pack = RenderedPack(
        files={
            "printer.cfg": "[include mcu.cfg]\n",
            "mcu.cfg": "[mcu]\n",
        }
    )
    service = ExportService()

    folder_path = tmp_path / "folder_out"
    zip_path = tmp_path / "pack.zip"

    service.export_folder(pack, str(folder_path))
    assert (folder_path / "printer.cfg").read_text(encoding="utf-8") == "[include mcu.cfg]\n"
    assert (folder_path / "mcu.cfg").exists()

    service.export_zip(pack, str(zip_path))
    assert zip_path.exists()
    with ZipFile(zip_path, "r") as archive:
        names = set(archive.namelist())
    assert {"printer.cfg", "mcu.cfg"}.issubset(names)

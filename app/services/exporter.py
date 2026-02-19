from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from app.domain.models import RenderedPack


class ExportService:
    def export_folder(self, pack: RenderedPack, path: str) -> None:
        out_dir = Path(path)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, contents in pack.files.items():
            target = out_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8")

    def export_zip(self, pack: RenderedPack, path: str) -> None:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(out_path, "w", compression=ZIP_DEFLATED) as archive:
            for name, contents in pack.files.items():
                archive.writestr(name, contents)


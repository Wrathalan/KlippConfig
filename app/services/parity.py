from __future__ import annotations

from collections import OrderedDict

from app.domain.models import RenderedPack, ValidationReport
from app.services.config_graph import ConfigGraphService
from app.services.klipper_ast import parse_klipper_config


class ParityService:
    """Compares generated output against an imported reference config graph."""

    IGNORE_KEYS = {"serial", "canbus_uuid"}

    def __init__(self) -> None:
        self.graph_service = ConfigGraphService()

    @staticmethod
    def _normalize_path(path: str) -> str:
        return path.replace("\\", "/").strip().lstrip("/")

    @staticmethod
    def _strip_inline_comment(value: str) -> str:
        text = value
        for marker in (" #", " ;"):
            idx = text.find(marker)
            if idx >= 0:
                text = text[:idx]
        return text.rstrip()

    def _normalize_value(self, value: str) -> str:
        lines = [self._strip_inline_comment(line).strip() for line in (value or "").splitlines()]
        lines = [line for line in lines if line]
        return "\n".join(lines)

    def _to_section_key_map(self, file_map: dict[str, str]) -> dict[str, dict[str, dict[str, str]]]:
        out: dict[str, dict[str, dict[str, str]]] = {}
        for raw_path, content in file_map.items():
            normalized = self._normalize_path(raw_path)
            if not normalized.lower().endswith(".cfg"):
                continue
            document = parse_klipper_config(content)
            section_map: dict[str, dict[str, str]] = OrderedDict()
            for section in document.sections:
                values: dict[str, str] = OrderedDict()
                for entry in section.entries:
                    if entry.kind != "key_value" or entry.key_value is None:
                        continue
                    value = entry.key_value.value
                    if entry.key_value.continuations:
                        value = "\n".join([value, *entry.key_value.continuations])
                    values[entry.key_value.key.strip().lower()] = value
                section_map[section.name.strip().lower()] = values
            out[normalized] = section_map
        return out

    def compare(
        self,
        generated: RenderedPack,
        imported_files: dict[str, str],
        *,
        imported_root_file: str,
        imported_include_graph: dict[str, list[str]] | None = None,
    ) -> ValidationReport:
        report = ValidationReport()
        generated_map = self._to_section_key_map(generated.files)
        imported_map = self._to_section_key_map(imported_files)
        imported_root = self._normalize_path(imported_root_file)

        if imported_include_graph:
            relevant_paths = set(
                self.graph_service.flatten_graph(imported_include_graph, imported_root)
            )
            imported_map = {
                path: sections
                for path, sections in imported_map.items()
                if path in relevant_paths
            }

        if imported_root not in generated_map:
            report.add(
                severity="blocking",
                code="PARITY_ROOT_FILE_MISSING",
                message=f"Generated output does not contain imported root file '{imported_root_file}'.",
                field=imported_root_file,
            )
            return report

        for file_path, imported_sections in imported_map.items():
            generated_sections = generated_map.get(file_path)
            if generated_sections is None:
                report.add(
                    severity="blocking",
                    code="PARITY_FILE_MISSING",
                    message=f"Generated output is missing '{file_path}'.",
                    field=file_path,
                )
                continue

            for section_name, imported_values in imported_sections.items():
                generated_values = generated_sections.get(section_name)
                if generated_values is None:
                    report.add(
                        severity="blocking",
                        code="PARITY_SECTION_MISSING",
                        message=f"Missing section '[{section_name}]' in '{file_path}'.",
                        field=file_path,
                    )
                    continue

                for key, imported_value in imported_values.items():
                    if key in self.IGNORE_KEYS:
                        continue
                    if key not in generated_values:
                        report.add(
                            severity="blocking",
                            code="PARITY_KEY_MISSING",
                            message=f"Missing key '{key}' in section '[{section_name}]' ({file_path}).",
                            field=file_path,
                        )
                        continue
                    normalized_imported = self._normalize_value(imported_value)
                    normalized_generated = self._normalize_value(generated_values[key])
                    if normalized_imported != normalized_generated:
                        report.add(
                            severity="blocking",
                            code="PARITY_VALUE_DIFF",
                            message=(
                                f"Value mismatch for '{key}' in section '[{section_name}]' "
                                f"({file_path})."
                            ),
                            field=file_path,
                        )

        for file_path in generated_map:
            if file_path not in imported_map:
                report.add(
                    severity="warning",
                    code="PARITY_EXTRA_FILE",
                    message=f"Generated extra config file not present in imported source: '{file_path}'.",
                    field=file_path,
                )

        return report

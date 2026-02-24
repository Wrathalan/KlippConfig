from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Literal


SECTION_PATTERN = re.compile(r"^\s*\[([^\]]+)\]\s*(?:[#;].*)?$")
KEY_VALUE_COLON_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$")
KEY_VALUE_EQUALS_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)\s*=\s*(.*)$")


EntryKind = Literal["blank", "comment", "raw", "key_value"]


@dataclass(slots=True)
class KlipperKeyValue:
    key: str
    value: str
    separator: Literal[":", "="] = ":"
    continuations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class KlipperEntry:
    kind: EntryKind
    raw: str = ""
    key_value: KlipperKeyValue | None = None


@dataclass(slots=True)
class KlipperSection:
    name: str
    entries: list[KlipperEntry] = field(default_factory=list)

    @property
    def is_include_section(self) -> bool:
        return self.name.strip().lower().startswith("include ")

    @property
    def include_target(self) -> str | None:
        if not self.is_include_section:
            return None
        _, _, target = self.name.partition(" ")
        target = target.strip()
        return target or None


@dataclass(slots=True)
class KlipperDocument:
    preamble: list[KlipperEntry] = field(default_factory=list)
    sections: list[KlipperSection] = field(default_factory=list)
    has_trailing_newline: bool = True

    def section_names(self) -> list[str]:
        return [section.name for section in self.sections]

    def to_section_key_map(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for section in self.sections:
            lowered = section.name.strip().lower()
            mapping: dict[str, str] = out.setdefault(lowered, {})
            for entry in section.entries:
                if entry.kind != "key_value" or entry.key_value is None:
                    continue
                value = entry.key_value.value
                if entry.key_value.continuations:
                    value = "\n".join([value, *entry.key_value.continuations])
                mapping[entry.key_value.key.strip().lower()] = value
        return out


def parse_klipper_config(text: str) -> KlipperDocument:
    lines = (text or "").splitlines()
    doc = KlipperDocument(has_trailing_newline=(text or "").endswith("\n"))
    current_section: KlipperSection | None = None
    current_target = doc.preamble
    last_key_value: KlipperKeyValue | None = None

    for raw_line in lines:
        stripped = raw_line.strip()
        section_match = SECTION_PATTERN.match(raw_line)
        if section_match:
            section = KlipperSection(name=section_match.group(1).strip())
            doc.sections.append(section)
            current_section = section
            current_target = section.entries
            last_key_value = None
            continue

        if not stripped:
            current_target.append(KlipperEntry(kind="blank", raw=""))
            last_key_value = None
            continue

        if stripped.startswith(("#", ";")):
            current_target.append(KlipperEntry(kind="comment", raw=raw_line))
            last_key_value = None
            continue

        if raw_line[:1].isspace() and last_key_value is not None:
            last_key_value.continuations.append(raw_line)
            continue

        if raw_line[:1].isspace():
            current_target.append(KlipperEntry(kind="raw", raw=raw_line))
            continue

        colon_match = KEY_VALUE_COLON_PATTERN.match(stripped)
        if colon_match:
            key_value = KlipperKeyValue(
                key=colon_match.group(1).strip(),
                value=colon_match.group(2).strip(),
                separator=":",
            )
            current_target.append(KlipperEntry(kind="key_value", key_value=key_value))
            last_key_value = key_value
            continue

        equals_match = KEY_VALUE_EQUALS_PATTERN.match(stripped)
        if equals_match:
            key_value = KlipperKeyValue(
                key=equals_match.group(1).strip(),
                value=equals_match.group(2).strip(),
                separator="=",
            )
            current_target.append(KlipperEntry(kind="key_value", key_value=key_value))
            last_key_value = key_value
            continue

        current_target.append(KlipperEntry(kind="raw", raw=raw_line))
        last_key_value = None

    return doc


def render_klipper_config(doc: KlipperDocument) -> str:
    lines: list[str] = []

    def emit_entry(entry: KlipperEntry) -> None:
        if entry.kind == "blank":
            lines.append("")
            return
        if entry.kind in {"comment", "raw"}:
            lines.append(entry.raw)
            return
        key_value = entry.key_value
        if key_value is None:
            lines.append(entry.raw)
            return
        lines.append(f"{key_value.key}{key_value.separator} {key_value.value}")
        lines.extend(key_value.continuations)

    for entry in doc.preamble:
        emit_entry(entry)

    for section in doc.sections:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"[{section.name}]")
        for entry in section.entries:
            emit_entry(entry)

    rendered = "\n".join(lines)
    if doc.has_trailing_newline:
        return rendered + "\n"
    return rendered


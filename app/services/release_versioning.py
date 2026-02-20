from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
from typing import Literal


BumpLevel = Literal["major", "minor", "patch"]

SEMVER_PATTERN = re.compile(r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")
PROJECT_VERSION_PATTERN = re.compile(r'(?m)^(version\s*=\s*")(?P<version>\d+\.\d+\.\d+)(")\s*$')
APP_VERSION_PATTERN = re.compile(r'(?m)^(__version__\s*=\s*")(?P<version>\d+\.\d+\.\d+)(")\s*$')
INNO_VERSION_PATTERN = re.compile(
    r'(?m)^(#define\s+MyAppVersion\s+")(?P<version>\d+\.\d+\.\d+)(")\s*$'
)


@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def parse_semver(raw: str) -> SemVer:
    value = raw.strip()
    match = SEMVER_PATTERN.match(value)
    if match is None:
        raise ValueError(f"Invalid semantic version: '{raw}'")
    return SemVer(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
    )


def bump_semver(current: SemVer, level: BumpLevel) -> SemVer:
    if level == "major":
        return SemVer(current.major + 1, 0, 0)
    if level == "minor":
        return SemVer(current.major, current.minor + 1, 0)
    if level == "patch":
        return SemVer(current.major, current.minor, current.patch + 1)
    raise ValueError(f"Unsupported bump level: {level}")


def _replace_single(text: str, pattern: re.Pattern[str], new_version: str, label: str) -> str:
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"Could not find {label} version field to update.")
    return pattern.sub(lambda m: f'{m.group(1)}{new_version}{m.group(3)}', text, count=1)


def replace_pyproject_version(text: str, new_version: str) -> str:
    return _replace_single(text, PROJECT_VERSION_PATTERN, new_version, "pyproject")


def replace_app_version(text: str, new_version: str) -> str:
    return _replace_single(text, APP_VERSION_PATTERN, new_version, "app/version.py")


def replace_inno_version(text: str, new_version: str) -> str:
    return _replace_single(text, INNO_VERSION_PATTERN, new_version, "Inno Setup script")


def read_pyproject_version(pyproject_path: Path) -> SemVer:
    text = pyproject_path.read_text(encoding="utf-8")
    match = PROJECT_VERSION_PATTERN.search(text)
    if match is None:
        raise ValueError(f"Could not find project version in {pyproject_path}.")
    return parse_semver(match.group("version"))


def ensure_changelog_exists(changelog_path: Path) -> None:
    if changelog_path.exists():
        return
    changelog_path.write_text(
        "# Changelog\n\n"
        "All notable changes to this project will be documented in this file.\n\n"
        "## [Unreleased]\n\n",
        encoding="utf-8",
    )


def insert_changelog_entry(
    changelog_text: str,
    version: str,
    summary: str,
    highlights: list[str],
    release_date: str | None = None,
) -> str:
    date_value = release_date or date.today().isoformat()
    cleaned_summary = summary.strip() or "Release updates."
    cleaned_highlights = [item.strip() for item in highlights if item.strip()]
    if not cleaned_highlights:
        cleaned_highlights = ["General improvements and fixes."]

    entry_lines = [
        f"## [{version}] - {date_value}",
        "",
        "### Summary",
        cleaned_summary,
        "",
        "### Highlights",
        *[f"- {item}" for item in cleaned_highlights],
        "",
    ]
    entry_text = "\n".join(entry_lines)

    marker = re.search(r"(?m)^## \[Unreleased\]\s*$", changelog_text)
    if marker is None:
        base = changelog_text.rstrip()
        if base:
            return f"{base}\n\n## [Unreleased]\n\n{entry_text}\n"
        return f"# Changelog\n\n## [Unreleased]\n\n{entry_text}\n"

    insert_after = marker.end()
    tail = changelog_text[insert_after:]
    next_heading = re.search(r"(?m)^## \[", tail)
    if next_heading is None:
        insert_at = len(changelog_text)
    else:
        insert_at = insert_after + next_heading.start()

    before = changelog_text[:insert_at].rstrip()
    after = changelog_text[insert_at:].lstrip("\n")
    if after:
        return f"{before}\n\n{entry_text}\n{after}"
    return f"{before}\n\n{entry_text}\n"


def build_github_announcement(
    *,
    version: str,
    summary: str,
    highlights: list[str],
    github_repo: str,
    discord_url: str,
    release_date: str | None = None,
) -> str:
    date_value = release_date or date.today().isoformat()
    cleaned_highlights = [item.strip() for item in highlights if item.strip()]
    if not cleaned_highlights:
        cleaned_highlights = ["General improvements and fixes."]
    release_tag_url = f"https://github.com/{github_repo}/releases/tag/v{version}"

    lines = [
        f"# KlippConfig v{version}",
        "",
        f"Release date: {date_value}",
        "",
        "## Summary",
        summary.strip() or "Release updates.",
        "",
        "## Highlights",
        *[f"- {item}" for item in cleaned_highlights],
        "",
        "## Download",
        f"- GitHub Release: {release_tag_url}",
        "",
        "## Community",
        f"- Discord: {discord_url}",
        "",
    ]
    return "\n".join(lines)


def build_discord_announcement(
    *,
    version: str,
    summary: str,
    highlights: list[str],
    github_repo: str,
    discord_url: str,
) -> str:
    cleaned_highlights = [item.strip() for item in highlights if item.strip()]
    if not cleaned_highlights:
        cleaned_highlights = ["General improvements and fixes."]
    release_tag_url = f"https://github.com/{github_repo}/releases/tag/v{version}"

    lines = [
        f"**KlippConfig v{version} released**",
        "",
        summary.strip() or "Release updates.",
        "",
        "**Highlights**",
        *[f"- {item}" for item in cleaned_highlights],
        "",
        f"Download: {release_tag_url}",
        f"Community: {discord_url}",
        "",
    ]
    return "\n".join(lines)


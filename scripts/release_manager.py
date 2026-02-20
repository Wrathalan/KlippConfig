from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import re
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.release_versioning import (
    bump_semver,
    build_discord_announcement,
    build_github_announcement,
    ensure_changelog_exists,
    insert_changelog_entry,
    parse_semver,
    read_pyproject_version,
    replace_app_version,
    replace_inno_version,
    replace_pyproject_version,
)


DEFAULT_DISCORD_URL = "https://discord.gg/bbnAtfbY5C"


def _repo_root() -> Path:
    return REPO_ROOT


def _infer_github_repo(default: str = "Wrathalan/KlippConfig") -> str:
    try:
        completed = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            check=True,
            capture_output=True,
            text=True,
            cwd=_repo_root(),
        )
    except Exception:  # noqa: BLE001
        return default

    remote_url = completed.stdout.strip()
    if not remote_url:
        return default

    https_match = re.search(r"github\.com[:/](?P<repo>[^/]+/[^/.]+)(?:\.git)?$", remote_url)
    if https_match:
        return https_match.group("repo")
    return default


def _write_text(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def cmd_show(_args: argparse.Namespace) -> int:
    root = _repo_root()
    current = read_pyproject_version(root / "pyproject.toml")
    print(f"Current version: {current}")
    return 0


def cmd_bump(args: argparse.Namespace) -> int:
    root = _repo_root()
    pyproject_path = root / "pyproject.toml"
    app_version_path = root / "app" / "version.py"
    inno_path = root / "scripts" / "klippconfig-installer.iss"
    changelog_path = root / "CHANGELOG.md"
    announce_dir = root / "release" / "announcements"

    current = read_pyproject_version(pyproject_path)
    if args.set_version:
        target = parse_semver(args.set_version)
    else:
        target = bump_semver(current, args.level)
    target_text = str(target)

    if target == current:
        print(f"Version unchanged ({target_text}).")
    else:
        print(f"Bumping version: {current} -> {target_text}")

    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    app_version_text = app_version_path.read_text(encoding="utf-8")
    inno_text = inno_path.read_text(encoding="utf-8")

    updated_pyproject = replace_pyproject_version(pyproject_text, target_text)
    updated_app_version = replace_app_version(app_version_text, target_text)
    updated_inno = replace_inno_version(inno_text, target_text)

    summary = args.summary.strip() or "Release updates."
    highlights = [item.strip() for item in args.highlight if item.strip()]
    if not highlights:
        highlights = ["General improvements and fixes."]
    release_date = date.today().isoformat()

    ensure_changelog_exists(changelog_path)
    changelog_text = changelog_path.read_text(encoding="utf-8")
    updated_changelog = insert_changelog_entry(
        changelog_text=changelog_text,
        version=target_text,
        summary=summary,
        highlights=highlights,
        release_date=release_date,
    )

    github_repo = args.github_repo or _infer_github_repo()
    discord_url = args.discord_url or DEFAULT_DISCORD_URL
    github_announcement = build_github_announcement(
        version=target_text,
        summary=summary,
        highlights=highlights,
        github_repo=github_repo,
        discord_url=discord_url,
        release_date=release_date,
    )
    discord_announcement = build_discord_announcement(
        version=target_text,
        summary=summary,
        highlights=highlights,
        github_repo=github_repo,
        discord_url=discord_url,
    )

    github_path = announce_dir / f"v{target_text}-github.md"
    discord_path = announce_dir / f"v{target_text}-discord.md"

    if args.dry_run:
        print("Dry run only. No files updated.")
        print(f"Would update: {pyproject_path}")
        print(f"Would update: {app_version_path}")
        print(f"Would update: {inno_path}")
        print(f"Would update: {changelog_path}")
        print(f"Would write:  {github_path}")
        print(f"Would write:  {discord_path}")
        return 0

    _write_text(pyproject_path, updated_pyproject)
    _write_text(app_version_path, updated_app_version)
    _write_text(inno_path, updated_inno)
    _write_text(changelog_path, updated_changelog)
    _write_text(github_path, github_announcement)
    _write_text(discord_path, discord_announcement)

    print("Updated files:")
    print(f"- {pyproject_path.relative_to(root)}")
    print(f"- {app_version_path.relative_to(root)}")
    print(f"- {inno_path.relative_to(root)}")
    print(f"- {changelog_path.relative_to(root)}")
    print(f"- {github_path.relative_to(root)}")
    print(f"- {discord_path.relative_to(root)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="KlippConfig release/version management helper.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    show_parser = subparsers.add_parser("show", help="Show current project version.")
    show_parser.set_defaults(func=cmd_show)

    bump_parser = subparsers.add_parser(
        "bump",
        help="Bump or set version, update release metadata, and generate announcements.",
    )
    level_group = bump_parser.add_mutually_exclusive_group(required=False)
    level_group.add_argument(
        "--level",
        choices=("major", "minor", "patch"),
        default="patch",
        help="Semantic version increment level (default: patch).",
    )
    level_group.add_argument(
        "--set-version",
        dest="set_version",
        help="Set an explicit semantic version (example: 0.2.0).",
    )
    bump_parser.add_argument(
        "--summary",
        default="Work in progress improvements and fixes.",
        help="One-line release summary.",
    )
    bump_parser.add_argument(
        "--highlight",
        action="append",
        default=[],
        help="Repeat to add highlight bullet(s).",
    )
    bump_parser.add_argument(
        "--github-repo",
        default=None,
        help="GitHub repo slug (owner/repo). Default: infer from origin.",
    )
    bump_parser.add_argument(
        "--discord-url",
        default=DEFAULT_DISCORD_URL,
        help=f"Discord invite URL (default: {DEFAULT_DISCORD_URL}).",
    )
    bump_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing files.",
    )
    bump_parser.set_defaults(func=cmd_bump)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

from app.services.release_versioning import (
    bump_semver,
    build_discord_announcement,
    build_github_announcement,
    insert_changelog_entry,
    parse_semver,
    replace_app_version,
    replace_inno_version,
    replace_pyproject_version,
)


def test_parse_and_bump_semver() -> None:
    current = parse_semver("1.2.3")
    assert str(current) == "1.2.3"
    assert str(bump_semver(current, "patch")) == "1.2.4"
    assert str(bump_semver(current, "minor")) == "1.3.0"
    assert str(bump_semver(current, "major")) == "2.0.0"


def test_replace_version_fields() -> None:
    pyproject_text = '[project]\nversion = "0.1.0"\n'
    assert 'version = "0.2.0"' in replace_pyproject_version(pyproject_text, "0.2.0")

    app_version_text = '__version__ = "0.1.0"\n'
    assert '__version__ = "0.2.0"' in replace_app_version(app_version_text, "0.2.0")

    inno_text = '#define MyAppVersion "0.1.0"\n'
    assert '#define MyAppVersion "0.2.0"' in replace_inno_version(inno_text, "0.2.0")


def test_insert_changelog_entry_under_unreleased() -> None:
    text = "# Changelog\n\n## [Unreleased]\n\n"
    updated = insert_changelog_entry(
        changelog_text=text,
        version="0.2.0",
        summary="Release summary",
        highlights=["Item one", "Item two"],
        release_date="2026-02-20",
    )
    assert "## [0.2.0] - 2026-02-20" in updated
    assert "- Item one" in updated
    assert "- Item two" in updated


def test_announcement_templates_include_expected_urls() -> None:
    github = build_github_announcement(
        version="0.2.0",
        summary="Summary",
        highlights=["One"],
        github_repo="Wrathalan/KlippConfig",
        discord_url="https://discord.gg/4CthQzS7Qy",
        release_date="2026-02-20",
    )
    assert "https://github.com/Wrathalan/KlippConfig/releases/tag/v0.2.0" in github
    assert "https://discord.gg/4CthQzS7Qy" in github

    discord = build_discord_announcement(
        version="0.2.0",
        summary="Summary",
        highlights=["One"],
        github_repo="Wrathalan/KlippConfig",
        discord_url="https://discord.gg/4CthQzS7Qy",
    )
    assert "KlippConfig v0.2.0 released" in discord
    assert "https://github.com/Wrathalan/KlippConfig/releases/tag/v0.2.0" in discord


from __future__ import annotations

from dataclasses import dataclass
import json
import re
import urllib.error
import urllib.request


LATEST_RELEASE_API_TEMPLATE = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
_VERSION_PART_PATTERN = re.compile(r"\d+")


class UpdateCheckError(RuntimeError):
    """Raised when update metadata cannot be fetched or parsed."""


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str
    latest_tag: str
    release_url: str
    update_available: bool


def normalize_version_tag(raw_value: str) -> str:
    return str(raw_value or "").strip().lstrip("vV")


def _version_tuple(raw_value: str) -> tuple[int, ...]:
    normalized = normalize_version_tag(raw_value)
    parts = [int(match.group(0)) for match in _VERSION_PART_PATTERN.finditer(normalized)]
    if not parts:
        raise UpdateCheckError(f"Unable to parse version: {raw_value!r}")
    return tuple(parts)


def is_newer_version(current_version: str, latest_version: str) -> bool:
    current_parts = _version_tuple(current_version)
    latest_parts = _version_tuple(latest_version)
    width = max(len(current_parts), len(latest_parts))
    current_padded = current_parts + (0,) * (width - len(current_parts))
    latest_padded = latest_parts + (0,) * (width - len(latest_parts))
    return latest_padded > current_padded


def check_latest_release(
    *,
    owner: str,
    repo: str,
    current_version: str,
    timeout_seconds: float = 4.0,
) -> UpdateCheckResult:
    api_url = LATEST_RELEASE_API_TEMPLATE.format(owner=owner.strip(), repo=repo.strip())
    request = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"KlippConfig/{normalize_version_tag(current_version) or 'unknown'}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", 200))
            if status >= 400:
                raise UpdateCheckError(f"GitHub returned HTTP {status}.")
            payload_bytes = response.read()
    except urllib.error.HTTPError as exc:
        raise UpdateCheckError(f"GitHub returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        detail = str(reason or exc)
        raise UpdateCheckError(f"Could not reach GitHub: {detail}") from exc
    except OSError as exc:
        raise UpdateCheckError(f"Failed to check updates: {exc}") from exc

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateCheckError(f"GitHub response could not be parsed: {exc}") from exc

    if not isinstance(payload, dict):
        raise UpdateCheckError("GitHub response was not a JSON object.")

    latest_tag = str(payload.get("tag_name") or "").strip()
    release_url = str(payload.get("html_url") or "").strip()
    if not latest_tag:
        raise UpdateCheckError("GitHub response was missing tag_name.")
    if not release_url:
        raise UpdateCheckError("GitHub response was missing html_url.")

    current = normalize_version_tag(current_version)
    latest = normalize_version_tag(latest_tag)
    update_available = is_newer_version(current, latest)
    return UpdateCheckResult(
        current_version=current,
        latest_version=latest,
        latest_tag=latest_tag,
        release_url=release_url,
        update_available=update_available,
    )

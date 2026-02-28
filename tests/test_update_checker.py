import json

import pytest

from app.services import update_checker


class _FakeResponse:
    def __init__(self, payload: dict[str, object], *, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_normalize_version_tag_strips_v_prefix() -> None:
    assert update_checker.normalize_version_tag("v0.1.5") == "0.1.5"
    assert update_checker.normalize_version_tag("V2.0.0") == "2.0.0"


def test_is_newer_version_compares_numeric_parts() -> None:
    assert update_checker.is_newer_version("0.1.5", "0.1.6") is True
    assert update_checker.is_newer_version("0.1.5", "v0.1.5") is False
    assert update_checker.is_newer_version("0.1", "0.1.0") is False
    assert update_checker.is_newer_version("0.2.0", "0.1.9") is False


def test_check_latest_release_parses_payload(monkeypatch) -> None:
    def _fake_urlopen(request, timeout):  # noqa: ANN001
        assert request.full_url.endswith("/repos/Wrathalan/KlippConfig/releases/latest")
        assert timeout == 4.0
        return _FakeResponse(
            {
                "tag_name": "v0.1.6",
                "html_url": "https://github.com/Wrathalan/KlippConfig/releases/tag/v0.1.6",
            }
        )

    monkeypatch.setattr(update_checker.urllib.request, "urlopen", _fake_urlopen)

    result = update_checker.check_latest_release(
        owner="Wrathalan",
        repo="KlippConfig",
        current_version="0.1.5",
    )

    assert result.latest_tag == "v0.1.6"
    assert result.latest_version == "0.1.6"
    assert result.update_available is True


def test_check_latest_release_raises_on_missing_payload_fields(monkeypatch) -> None:
    def _fake_urlopen(_request, timeout):  # noqa: ANN001
        assert timeout == 4.0
        return _FakeResponse({"tag_name": "v0.1.6"})

    monkeypatch.setattr(update_checker.urllib.request, "urlopen", _fake_urlopen)

    with pytest.raises(update_checker.UpdateCheckError):
        update_checker.check_latest_release(
            owner="Wrathalan",
            repo="KlippConfig",
            current_version="0.1.5",
        )

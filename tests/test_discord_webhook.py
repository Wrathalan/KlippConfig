import json

import pytest

from app.services.discord_webhook import chunk_discord_content, post_discord_webhook


class _DummyResponse:
    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN204
        return False


def test_chunk_discord_content_splits_long_message() -> None:
    message = ("line one\n" * 180).strip()
    chunks = chunk_discord_content(message, limit=200)
    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)


def test_chunk_discord_content_empty_message_returns_no_chunks() -> None:
    assert chunk_discord_content("   ") == []


def test_post_discord_webhook_posts_all_chunks() -> None:
    sent_payloads: list[dict[str, str]] = []
    sent_user_agents: list[str | None] = []

    def fake_sender(req, timeout):  # noqa: ANN001
        assert timeout == 15.0
        payload = json.loads(req.data.decode("utf-8"))
        sent_payloads.append(payload)
        sent_user_agents.append(req.get_header("User-agent"))
        return _DummyResponse()

    count = post_discord_webhook(
        "https://discord.com/api/webhooks/example/token",
        "alpha\n" * 900,
        sender=fake_sender,
    )

    assert count == len(sent_payloads)
    assert count > 1
    assert all("content" in payload and payload["content"] for payload in sent_payloads)
    assert all(agent for agent in sent_user_agents)


def test_post_discord_webhook_rejects_empty_url() -> None:
    with pytest.raises(ValueError, match="Discord webhook URL is required."):
        post_discord_webhook("   ", "hello")


def test_post_discord_webhook_rejects_empty_content() -> None:
    with pytest.raises(ValueError, match="Discord message content is empty."):
        post_discord_webhook("https://discord.com/api/webhooks/example/token", "   ")


def test_post_discord_webhook_wraps_sender_failures() -> None:
    def failing_sender(_req, _timeout):  # noqa: ANN001
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError, match="Failed to post Discord webhook message:"):
        post_discord_webhook(
            "https://discord.com/api/webhooks/example/token",
            "hello",
            sender=failing_sender,
        )

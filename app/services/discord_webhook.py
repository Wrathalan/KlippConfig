from __future__ import annotations

import json
from typing import Callable, Iterable
from urllib import request


DISCORD_MESSAGE_LIMIT = 2000
DEFAULT_USER_AGENT = "KlippConfig-ReleaseBot/1.0 (+https://github.com/Wrathalan/KlippConfig)"


def chunk_discord_content(message: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    text = message.strip()
    if not text:
        return []
    if limit < 50:
        raise ValueError("Discord chunk limit is too small.")

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at <= 0:
            split_at = limit

        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:limit]
            split_at = limit
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()
    return chunks


def post_discord_webhook(
    webhook_url: str,
    content: str,
    *,
    timeout: float = 15.0,
    sender: Callable[[request.Request, float], object] | None = None,
) -> int:
    url = webhook_url.strip()
    if not url:
        raise ValueError("Discord webhook URL is required.")

    chunks = chunk_discord_content(content)
    if not chunks:
        raise ValueError("Discord message content is empty.")

    call = sender or request.urlopen
    sent_count = 0
    for chunk in chunks:
        payload = json.dumps({"content": chunk}).encode("utf-8")
        req = request.Request(
            url=url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": DEFAULT_USER_AGENT,
            },
            method="POST",
        )
        try:
            with call(req, timeout=timeout):
                sent_count += 1
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Failed to post Discord webhook message: {exc}") from exc
    return sent_count

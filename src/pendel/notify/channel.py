"""Notification channels (charter goal G3): a Channel interface plus Telegram
bot and ntfy topic adapters, and a FakeChannel for tests.

Both real adapters take an injected httpx.Client so no adapter ever creates
its own connection, and every HTTP failure is wrapped in ChannelSendError
with a message that names only the channel kind and the status code or
exception type - never the request URL or the bot token/server, which would
otherwise leak through httpx's default exception messages.
"""

from __future__ import annotations

import os
import re
from typing import Protocol

import httpx

_TELEGRAM_API_BASE = "https://api.telegram.org"
_NTFY_TOPIC_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Channel(Protocol):
    """Something a notification can be sent through."""

    def send(self, target: str, text: str) -> None: ...


class ChannelSendError(Exception):
    """A send() call failed. The message never contains the request URL,
    the bot token or the ntfy server, only the channel kind and the HTTP
    status code or transport exception type."""


class FakeChannel:
    """In-memory Channel for tests: records every send() call, sends nothing."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, target: str, text: str) -> None:
        self.sent.append((target, text))


class TelegramChannel:
    """Sends via the Telegram Bot API. `target` is the chat_id."""

    def __init__(self, http_client: httpx.Client, *, bot_token: str | None = None) -> None:
        self._http = http_client
        token = bot_token or os.environ.get("PENDEL_TELEGRAM_BOT_TOKEN")
        if not token:
            raise ValueError(
                "PENDEL_TELEGRAM_BOT_TOKEN is not set and no bot_token was given"
            )
        self._token = token

    def send(self, target: str, text: str) -> None:
        url = f"{_TELEGRAM_API_BASE}/bot{self._token}/sendMessage"
        try:
            response = self._http.post(url, json={"chat_id": target, "text": text})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ChannelSendError(
                f"telegram send failed: HTTP {exc.response.status_code}"
            ) from None
        except httpx.HTTPError as exc:
            raise ChannelSendError(f"telegram send failed: {type(exc).__name__}") from None


class NtfyChannel:
    """Sends via an ntfy server. `target` is the topic, restricted to
    ^[A-Za-z0-9_-]{1,64}$, validated before any request is built."""

    def __init__(self, http_client: httpx.Client, *, server: str | None = None) -> None:
        self._http = http_client
        server_url = server or os.environ.get("PENDEL_NTFY_URL")
        if not server_url:
            raise ValueError("PENDEL_NTFY_URL is not set and no server was given")
        self._server = server_url.rstrip("/")

    def send(self, target: str, text: str) -> None:
        if _NTFY_TOPIC_RE.fullmatch(target) is None:
            raise ValueError(f"invalid ntfy topic: {target!r}")
        url = f"{self._server}/{target}"
        try:
            response = self._http.post(url, content=text.encode("utf-8"))
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ChannelSendError(
                f"ntfy send failed: HTTP {exc.response.status_code}"
            ) from None
        except httpx.HTTPError as exc:
            raise ChannelSendError(f"ntfy send failed: {type(exc).__name__}") from None

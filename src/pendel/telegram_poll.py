"""Telegram getUpdates poller (charter goal G3): `python -m pendel.telegram_poll`
fetches Telegram updates and feeds them into `pendel.telegram_link.handle_update`,
so a user who opens the `/start` deep link is actually linked. `create_link` and
`handle_update` were tested against a fake but nothing in production ever
called them with real Telegram updates -- this module is that missing glue.

Tested only through `httpx.MockTransport`; this module never contacts
api.telegram.org and no real bot token is ever created (charter constraint).
The poll offset is kept in memory only, no migration is added: `handle_update`
is idempotent (tokens are single-use and a replayed `/start` gets the same
neutral reply), so replaying updates after a restart is harmless.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from collections.abc import Callable
from typing import Any

import httpx

from pendel import db
from pendel.notify import Channel, TelegramChannel
from pendel.notify.channel import _TELEGRAM_API_BASE
from pendel.telegram_link import handle_update

logger = logging.getLogger(__name__)

_TOKEN_ENV = "PENDEL_TELEGRAM_BOT_TOKEN"


class TelegramPollError(RuntimeError):
    """A getUpdates call failed. The message never contains the request URL
    or the bot token, only a short description of what went wrong."""


def poll_once(
    conn: sqlite3.Connection,
    http_client: httpx.Client,
    bot_token: str,
    channel: Channel,
    offset: int,
    *,
    long_poll_s: int = 25,
) -> int:
    """Fetch one batch of updates via getUpdates and feed each to
    `handle_update`. Returns the offset to use for the next call: one past
    the highest update_id seen, or the unchanged `offset` when there were no
    updates. An update that makes `handle_update` raise is logged and
    skipped; it does not stop the rest of the batch and the offset still
    advances past it."""
    url = f"{_TELEGRAM_API_BASE}/bot{bot_token}/getUpdates"
    try:
        response = http_client.get(
            url, params={"offset": offset, "timeout": long_poll_s}
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise TelegramPollError(
            f"telegram getUpdates failed: HTTP {exc.response.status_code}"
        ) from None
    except httpx.HTTPError as exc:
        raise TelegramPollError(
            f"telegram getUpdates failed: {type(exc).__name__}"
        ) from None

    try:
        payload: Any = response.json()
    except ValueError:
        raise TelegramPollError("telegram getUpdates failed: invalid JSON body") from None

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise TelegramPollError("telegram getUpdates failed: ok is not true")

    updates = payload.get("result")
    if not isinstance(updates, list):
        raise TelegramPollError("telegram getUpdates failed: result is not a list")

    next_offset = offset
    for update in updates:
        if not isinstance(update, dict):
            logger.warning("skipping malformed telegram update: not an object")
            continue
        update_id = update.get("update_id")
        if isinstance(update_id, int) and update_id + 1 > next_offset:
            next_offset = update_id + 1
        try:
            handle_update(conn, update, channel)
        except Exception:
            logger.exception("skipping telegram update that raised in handle_update")

    return next_offset


def run_forever(
    conn: sqlite3.Connection,
    http_client: httpx.Client,
    bot_token: str,
    channel: Channel,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Loop `poll_once` forever, advancing the in-memory offset. Any
    exception -- including a `TelegramPollError` from a Telegram outage -- is
    logged and followed by `sleep(5)` instead of ending the loop."""
    offset = 0
    while True:
        try:
            offset = poll_once(conn, http_client, bot_token, channel, offset)
        except Exception:
            logger.exception("telegram poll failed; backing off")
            sleep(5)


def main() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO)
    # httpx logs every request (method + full URL, including the bot token
    # embedded in the path) at INFO on the "httpx"/"httpcore" loggers; keep
    # those above INFO so the token never reaches stdout/container logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    token = os.environ.get(_TOKEN_ENV)
    if not token:
        logger.info("%s is not set; telegram poller disabled", _TOKEN_ENV)
        return

    conn = db.connect()
    long_poll_s = 25
    http_client = httpx.Client(timeout=long_poll_s + 10)
    channel = TelegramChannel(http_client, bot_token=token)
    run_forever(conn, http_client, token, channel)


if __name__ == "__main__":
    main()

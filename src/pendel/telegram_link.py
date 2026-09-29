"""Telegram /start deep-link linking flow (charter goal G3).

A user gets a `https://t.me/<bot>?start=<token>` link (`create_link`), opens
it in Telegram, and the resulting `/start <token>` update is fed to
`handle_update` along with the real `notify.Channel` used to talk to
Telegram. Linking never contacts api.telegram.org itself: `create_link`
only writes a row via the db API from T-0006, and `handle_update` only
calls the injected `Channel.send`.

Tokens are single-use: a matching, not-yet-linked row is linked and its
`link_token` cleared in the same update; any other text -- an unknown,
already-used or malformed token -- links nothing and gets the same neutral
reply, so a reply never reveals whether a token existed.
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
from typing import Any

from pendel.notify import Channel

_BOT_USERNAME_ENV = "PENDEL_TELEGRAM_BOT_USERNAME"

# Telegram sends deep-link payloads as "/start <payload>"; a bot mention
# ("/start@my_bot <payload>") is possible in some clients too.
_START_RE = re.compile(r"^/start(?:@\w+)?(?:\s+(\S+))?$")

CONFIRMATION_TEXT = (
    "✅ Verbunden! Du bekommst ab jetzt Störungsmeldungen für deine "
    "gespeicherten Verbindungen per Telegram.\n"
    "✅ Linked! You will now receive disruption alerts for your saved "
    "commutes via Telegram."
)

INVALID_TOKEN_TEXT = (
    "Dieser Link ist ungültig oder wurde bereits verwendet. Bitte fordere "
    "einen neuen Link an.\n"
    "This link is invalid or has already been used. Please request a new one."
)


class TelegramLinkConfigError(RuntimeError):
    """Raised when PENDEL_TELEGRAM_BOT_USERNAME is not configured."""


def create_link(conn: sqlite3.Connection, user_id: str) -> str:
    """Create an unlinked telegram channel row for `user_id` and return its
    /start deep link. Raises TelegramLinkConfigError if the bot username
    (PENDEL_TELEGRAM_BOT_USERNAME) is not configured."""
    bot_username = os.environ.get(_BOT_USERNAME_ENV)
    if not bot_username:
        raise TelegramLinkConfigError(
            f"{_BOT_USERNAME_ENV} is not set; cannot build a Telegram deep link"
        )
    link_token = secrets.token_urlsafe(16)
    conn.execute(
        "INSERT INTO channels (user_id, kind, target, link_token) "
        "VALUES (?, 'telegram', NULL, ?)",
        (user_id, link_token),
    )
    conn.commit()
    return f"https://t.me/{bot_username}?start={link_token}"


def handle_update(conn: sqlite3.Connection, update: dict[str, Any], channel: Channel) -> None:
    """Process a Telegram Update-shaped dict. Links the channel row for a
    valid, unused token and sends exactly one confirmation; any other
    /start attempt gets the same neutral reply and links nothing. Updates
    that are not a /start command are ignored entirely (no reply)."""
    message = update.get("message")
    if not isinstance(message, dict):
        return
    text = message.get("text")
    if not isinstance(text, str):
        return
    match = _START_RE.match(text.strip())
    if match is None:
        return
    chat = message.get("chat")
    if not isinstance(chat, dict) or "id" not in chat:
        return
    chat_id = str(chat["id"])

    presented_token = match.group(1)
    row = None
    if presented_token:
        row = conn.execute(
            "SELECT id FROM channels WHERE kind = 'telegram' AND link_token = ? "
            "AND linked_at IS NULL",
            (presented_token,),
        ).fetchone()

    if row is None:
        channel.send(chat_id, INVALID_TOKEN_TEXT)
        return

    conn.execute(
        "UPDATE channels SET target = ?, linked_at = datetime('now'), link_token = NULL "
        "WHERE id = ?",
        (chat_id, row["id"]),
    )
    conn.commit()
    channel.send(chat_id, CONFIRMATION_TEXT)

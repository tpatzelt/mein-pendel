"""Disruption state tracker (charter G3): notify once per disruption, and a
resolved message when it clears.

`process` diffs a commute's current `engine.Verdict` against the
`notifications` table:

- a `disruption_key` that is not yet recorded as `active` -> send one
  message (DE+EN reason) to every linked channel and record the row as
  `active` (this also covers a key that comes back after being marked
  `resolved`: the row is reactivated, `resolved_at` cleared).
- a key that is already `active` -> do nothing, it was already notified.
- an `active` row whose key is no longer present in the verdict -> send
  one resolved message and mark the row `resolved` with `resolved_at`.

Each key's send and state change are committed together, right after the
send, so a channel failure partway through a tick never leaves a message
sent without its state recorded (which would cause a duplicate resend on
the next tick) or a state change committed without its message having
been sent.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Mapping

from pendel.engine import Verdict
from pendel.notify import Channel

_RESOLVED_DE = "Störung auf {line} behoben."
_RESOLVED_EN = "Disruption on {line} resolved."


def _recipients(conn: sqlite3.Connection, user_id: str) -> list[tuple[str, str]]:
    """Linked channels for `user_id`. A channel row created ahead of the
    Telegram /start deep-link (or before an ntfy topic is confirmed) has
    `target IS NULL` and must never be sent to."""
    rows = conn.execute(
        "SELECT kind, target FROM channels WHERE user_id = ? AND target IS NOT NULL",
        (user_id,),
    ).fetchall()
    return [(row["kind"], row["target"]) for row in rows]


def _send_to_all(
    channels: Mapping[str, Channel], recipients: list[tuple[str, str]], text: str
) -> None:
    for kind, target in recipients:
        channels[kind].send(target, text)


def _line_from_key(disruption_key: str) -> str:
    """`disruption_key` is `"{kind}:{line}:{...}"` (see engine._Event.key)."""
    parts = disruption_key.split(":")
    return parts[1] if len(parts) > 1 else disruption_key


def process(
    conn: sqlite3.Connection,
    commute_id: int,
    verdict: Verdict,
    channels: Mapping[str, Channel],
    now: dt.datetime,
) -> None:
    """Send at most one message per disruption per state change for
    `commute_id` and persist the new state, comparing `verdict` against the
    `notifications` rows currently `active` for this commute."""
    user_row = conn.execute(
        "SELECT user_id FROM commutes WHERE id = ?", (commute_id,)
    ).fetchone()
    if user_row is None:
        raise ValueError(f"no commute with id {commute_id}")
    recipients = _recipients(conn, user_row["user_id"])

    active_rows = conn.execute(
        "SELECT disruption_key FROM notifications WHERE commute_id = ? AND state = 'active'",
        (commute_id,),
    ).fetchall()
    active_keys = {row["disruption_key"] for row in active_rows}
    current_keys = {verdict.disruption_key} if verdict.affected else set()
    now_iso = now.isoformat()

    for key in current_keys - active_keys:
        _send_to_all(channels, recipients, f"{verdict.reason_de}\n{verdict.reason_en}")
        conn.execute(
            "INSERT INTO notifications (commute_id, disruption_key, state, first_notified_at, resolved_at) "
            "VALUES (?, ?, 'active', ?, NULL) "
            "ON CONFLICT (commute_id, disruption_key) DO UPDATE SET "
            "state = 'active', first_notified_at = excluded.first_notified_at, resolved_at = NULL",
            (commute_id, key, now_iso),
        )
        conn.commit()

    for key in active_keys - current_keys:
        line = _line_from_key(key)
        text = f"{_RESOLVED_DE.format(line=line)}\n{_RESOLVED_EN.format(line=line)}"
        _send_to_all(channels, recipients, text)
        conn.execute(
            "UPDATE notifications SET state = 'resolved', resolved_at = ? "
            "WHERE commute_id = ? AND disruption_key = ?",
            (now_iso, commute_id, key),
        )
        conn.commit()

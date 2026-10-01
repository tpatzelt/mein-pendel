"""Disruption state tracker (charter G3, G5): notify once per disruption, and a
resolved message when it clears.

`process` diffs a commute's current `engine.Verdict` against the
`notifications` table:

- a `disruption_key` that is not yet recorded as `active` -> send one
  message (DE+EN, naming the commute, line, planned time and reason --
  charter G5) to every linked channel and record the row as `active`
  (this also covers a key that comes back after being marked `resolved`:
  the row is reactivated, `resolved_at` cleared).
- a key that is already `active` -> do nothing, it was already notified.
- an `active` row whose key is no longer present in the verdict -> send
  one resolved message (DE+EN, naming the commute, what cleared -- a
  kind-specific phrase, charter G5 -- and the line, then a /today link)
  and mark the row `resolved` with `resolved_at`.

Each key's send and state change are committed together, right after the
send, so a channel failure partway through a tick never leaves a message
sent without its state recorded (which would cause a duplicate resend on
the next tick) or a state change committed without its message having
been sent.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
from collections.abc import Mapping

from pendel.commute import BERLIN
from pendel.engine import Verdict
from pendel.notify import Channel

_RESOLVED_DE = "Störung auf {line} behoben."
_RESOLVED_EN = "Disruption on {line} resolved."

# Per-kind resolved phrasing (charter G5: "the resolved message says what
# cleared"), keyed the same as engine._KIND_ORDER. Every DE phrase keeps
# ending in "behoben." and every EN phrase keeps the word "resolved" --
# only the noun naming what kind of disruption it was changes -- since
# test_scheduler.py (outside this task's allowed paths) already asserts
# those two words appear in a resolved message. "warning" is omitted
# since its own disruption phrase is already the generic "Störung" one,
# so it shares _RESOLVED_DE/_RESOLVED_EN with any kind this tracker
# doesn't recognise (e.g. a disruption_key from a future engine version).
_RESOLVED_KIND_DE = {
    "cancellation": "Ausfall auf {line} behoben.",
    "delay": "Verspätung auf {line} behoben.",
    "replacement_service": "Ersatzverkehr auf {line} behoben.",
    "construction": "Bauarbeiten auf {line} behoben.",
}
_RESOLVED_KIND_EN = {
    "cancellation": "Cancellation on {line} resolved.",
    "delay": "Delay on {line} resolved.",
    "replacement_service": "Replacement service on {line} resolved.",
    "construction": "Construction on {line} resolved.",
}

_GENERIC_NAME_DE = "Deine Verbindung"
_GENERIC_NAME_EN = "Your commute"

# A notification is read in Telegram or ntfy, where a bare "/today" is not a
# link, so it is prefixed with the deployment's public origin when one is set.
_PUBLIC_URL_ENV = "PENDEL_PUBLIC_URL"
_TODAY_PATH = "/today"


def today_url() -> str:
    """`PENDEL_PUBLIC_URL` + "/today", or the bare path when it is unset."""
    base = os.environ.get(_PUBLIC_URL_ENV, "").strip().rstrip("/")
    return f"{base}{_TODAY_PATH}"


def format_disruption_message(commute_name: str, verdict: Verdict, today_url: str) -> str:
    """The new-disruption message (charter G5): a German block naming the
    commute, the line, the planned departure time (HH:MM, Europe/Berlin)
    and the reason, then the same in English, then a link to `today_url`.
    The scheduler already appends the chosen alternative's summary to
    `verdict.reason_de`/`reason_en` (see `scheduler._with_alternative`), so
    the alternative rides along inside the reason.

    `commute_name` is expected as 'origin name → destination name'; an
    empty string (no stored names) falls back to a generic name, localized
    per block. `verdict.planned` of `None` renders as '?'."""
    name_de = commute_name or _GENERIC_NAME_DE
    name_en = commute_name or _GENERIC_NAME_EN
    planned = verdict.planned.astimezone(BERLIN).strftime("%H:%M") if verdict.planned else "?"
    de = f"{name_de}: Linie {verdict.line}, geplant {planned} Uhr. {verdict.reason_de}"
    en = f"{name_en}: line {verdict.line}, planned {planned}. {verdict.reason_en}"
    return f"{de}\n{en}\n{today_url}"


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


def _kind_from_key(disruption_key: str) -> str:
    """The `"{kind}:..."` prefix of `disruption_key` (see engine._Event.key)."""
    return disruption_key.split(":", 1)[0]


def format_resolved_message(commute_name: str, disruption_key: str, today_url: str) -> str:
    """The cleared-disruption message (charter G5): a German block naming
    the commute and what cleared (kind-specific phrase, e.g. 'Ausfall auf
    S41 behoben.' for a cancellation), then the same in English, then a
    link to `today_url`. The kind and line are read from `disruption_key`
    (no stored migration column for either); a kind this tracker doesn't
    recognise falls back to the generic '{line} disruption resolved'
    phrasing. `commute_name` behaves as in `format_disruption_message`."""
    name_de = commute_name or _GENERIC_NAME_DE
    name_en = commute_name or _GENERIC_NAME_EN
    line = _line_from_key(disruption_key)
    kind = _kind_from_key(disruption_key)
    phrase_de = _RESOLVED_KIND_DE.get(kind, _RESOLVED_DE).format(line=line)
    phrase_en = _RESOLVED_KIND_EN.get(kind, _RESOLVED_EN).format(line=line)
    de = f"{name_de}: {phrase_de}"
    en = f"{name_en}: {phrase_en}"
    return f"{de}\n{en}\n{today_url}"


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
    commute_row = conn.execute(
        "SELECT user_id, origin_name, destination_name FROM commutes WHERE id = ?",
        (commute_id,),
    ).fetchone()
    if commute_row is None:
        raise ValueError(f"no commute with id {commute_id}")
    recipients = _recipients(conn, commute_row["user_id"])
    origin_name = commute_row["origin_name"]
    destination_name = commute_row["destination_name"]
    commute_name = f"{origin_name} → {destination_name}" if origin_name and destination_name else ""

    active_rows = conn.execute(
        "SELECT disruption_key FROM notifications WHERE commute_id = ? AND state = 'active'",
        (commute_id,),
    ).fetchall()
    active_keys = {row["disruption_key"] for row in active_rows}
    current_keys = {verdict.disruption_key} if verdict.affected else set()
    now_iso = now.isoformat()

    for key in current_keys - active_keys:
        text = format_disruption_message(commute_name, verdict, today_url())
        _send_to_all(channels, recipients, text)
        conn.execute(
            "INSERT INTO notifications (commute_id, disruption_key, state, first_notified_at, resolved_at) "
            "VALUES (?, ?, 'active', ?, NULL) "
            "ON CONFLICT (commute_id, disruption_key) DO UPDATE SET "
            "state = 'active', first_notified_at = excluded.first_notified_at, resolved_at = NULL",
            (commute_id, key, now_iso),
        )
        conn.commit()

    for key in active_keys - current_keys:
        text = format_resolved_message(commute_name, key, today_url())
        _send_to_all(channels, recipients, text)
        conn.execute(
            "UPDATE notifications SET state = 'resolved', resolved_at = ? "
            "WHERE commute_id = ? AND disruption_key = ?",
            (now_iso, commute_id, key),
        )
        conn.commit()

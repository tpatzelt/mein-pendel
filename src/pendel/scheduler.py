"""Scheduler (charter G3): checks each saved commute shortly before its
departure window and notifies only when the engine says it is affected and
only once per disruption.

Clock, HafasClient and the notification channels are all injected so the
whole thing can be driven by tests without a live network call or a real
sleep. `tick(now)` does one pass over the due commutes; `run_forever` is a
thin loop around it and is never started in tests.

Error isolation: a HafasError fetching one stop's departures does not stop
other stops in the same tick, an exception processing one commute does not
stop the other commutes, and an exception escaping `tick` does not stop
`run_forever` -- see `tick` and `run_forever` below.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import math
import os
import sqlite3
import time as _time
from collections.abc import Callable

from pendel import alternatives, tracker
from pendel.commute import BERLIN, Commute
from pendel.engine import evaluate
from pendel.hafas import HafasClient, HafasError
from pendel.notify import Channel

logger = logging.getLogger(__name__)

_UTC = dt.timezone.utc
_DEFAULT_LEAD_MIN = 30
_LEAD_ENV_VAR = "PENDEL_CHECK_LEAD_MIN"


def _lead_minutes_from_env() -> int:
    raw = os.environ.get(_LEAD_ENV_VAR)
    if raw is None:
        return _DEFAULT_LEAD_MIN
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{_LEAD_ENV_VAR} must be an integer, got {raw!r}") from exc


def _commute_from_row(row: sqlite3.Row) -> Commute:
    return Commute(
        origin_stop_id=row["origin_stop_id"],
        destination_stop_id=row["destination_stop_id"],
        lines=frozenset(row["lines"].split(",")),
        weekdays=frozenset(int(day) for day in row["weekdays"].split(",")),
        window_start=dt.time.fromisoformat(row["window_start"]),
        window_end=dt.time.fromisoformat(row["window_end"]),
        delay_threshold_min=row["delay_threshold_min"],
    )


@dataclasses.dataclass(frozen=True)
class _DueCommute:
    commute_id: int
    commute: Commute
    window_end_utc: dt.datetime


class Scheduler:
    """Drives one tick of the notification pipeline, or a bounded/unbounded
    loop of ticks via `run_forever`."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        hafas_client: HafasClient,
        channels: dict[str, Channel],
        clock: Callable[[], dt.datetime],
        sleep: Callable[[float], None] = _time.sleep,
        interval_seconds: float = 60.0,
    ) -> None:
        self._conn = conn
        self._hafas = hafas_client
        self._channels = channels
        self._clock = clock
        self._sleep = sleep
        self._interval_seconds = interval_seconds
        # Validated once here so a bad env var fails fast at construction,
        # not partway through the first tick.
        self._lead_minutes = _lead_minutes_from_env()

    def run_forever(self) -> None:
        """Ticks forever on the injected clock/sleep. A tick that raises is
        logged and never ends the loop; sleep always runs afterwards so the
        interval is respected even when a tick failed."""
        while True:
            try:
                self.tick(self._clock())
            except Exception:
                logger.exception("scheduler tick failed")
            finally:
                self._sleep(self._interval_seconds)

    def tick(self, now: dt.datetime) -> None:
        """One pass: find due commutes, fetch departures once per origin
        stop, and evaluate/notify each commute. A HafasError for one stop
        only skips that stop's commutes; an exception processing one
        commute only skips that commute."""
        due_by_stop: dict[str, list[_DueCommute]] = {}
        for row in self._all_commute_rows():
            due = self._due_commute(row, now)
            if due is not None:
                due_by_stop.setdefault(due.commute.origin_stop_id, []).append(due)

        for stop_id, due_commutes in due_by_stop.items():
            latest_end_utc = max(item.window_end_utc for item in due_commutes)
            duration_minutes = max(
                1, math.ceil((latest_end_utc - now.astimezone(_UTC)).total_seconds() / 60)
            )
            try:
                departures_json = self._hafas.departures(
                    stop_id, now.astimezone(BERLIN), duration=duration_minutes
                )
            except HafasError:
                logger.exception("fetching departures for stop %s failed", stop_id)
                continue

            for due in due_commutes:
                try:
                    self._process_commute(due, departures_json, now)
                except Exception:
                    logger.exception("processing commute %s failed", due.commute_id)

    def _all_commute_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT id, origin_stop_id, destination_stop_id, lines, weekdays, "
            "window_start, window_end, delay_threshold_min FROM commutes"
        ).fetchall()

    def _due_commute(self, row: sqlite3.Row, now: dt.datetime) -> _DueCommute | None:
        commute = _commute_from_row(row)
        today = now.astimezone(BERLIN).date()
        if not commute.is_active_on(today):
            return None
        start, end = commute.window_bounds(today)
        start_utc = start.astimezone(_UTC)
        end_utc = end.astimezone(_UTC)
        now_utc = now.astimezone(_UTC)
        lead = dt.timedelta(minutes=self._lead_minutes)
        if start_utc - lead <= now_utc <= end_utc:
            return _DueCommute(commute_id=row["id"], commute=commute, window_end_utc=end_utc)
        return None

    def _process_commute(self, due: _DueCommute, departures_json, now: dt.datetime) -> None:
        verdict = evaluate(
            due.commute,
            departures_json,
            now,
            delay_threshold_min=due.commute.delay_threshold_min,
        )
        if verdict.affected:
            verdict = self._with_alternative(due, verdict, now)
        tracker.process(self._conn, due.commute_id, verdict, self._channels, now)

    def _with_alternative(self, due: _DueCommute, verdict, now: dt.datetime):
        # Rate-limit hygiene: once a disruption is already active for this
        # commute, tracker.process will not send anything new for it, so
        # there is no point paying for a fresh journeys() lookup.
        if self._disruption_already_active(due.commute_id, verdict.disruption_key):
            return verdict
        try:
            journeys_json = self._hafas.journeys(
                due.commute.origin_stop_id,
                due.commute.destination_stop_id,
                now.astimezone(BERLIN),
            )
        except HafasError:
            logger.exception("fetching alternatives for commute %s failed", due.commute_id)
            return verdict
        alt = alternatives.suggest_alternative(due.commute, verdict, journeys_json)
        if alt is None:
            return verdict
        return dataclasses.replace(
            verdict,
            reason_de=f"{verdict.reason_de} {alt.summary_de}",
            reason_en=f"{verdict.reason_en} {alt.summary_en}",
        )

    def _disruption_already_active(self, commute_id: int, disruption_key: str) -> bool:
        if not disruption_key:
            return False
        row = self._conn.execute(
            "SELECT state FROM notifications WHERE commute_id = ? AND disruption_key = ?",
            (commute_id, disruption_key),
        ).fetchone()
        return row is not None and row["state"] == "active"

"""A saved commute: origin/destination stops, lines ridden, weekdays and a
departure window, all in Europe/Berlin time (see charter G1).

DST semantics
-------------
Building a local Berlin datetime from a plain `datetime.date` + `datetime.time`
uses fold=0, i.e. the *first* wall-clock occurrence of an ambiguous time
during the autumn fold-back, and the pre-transition (winter) offset for a
wall-clock time that does not exist during the spring gap. `_normalise`
then round-trips that datetime through UTC and back to Europe/Berlin. For
an ambiguous (repeated) time this is a no-op: the instant already resolves
correctly. For a nonexistent (skipped) time it rewrites the value to the
wall-clock time that instant actually has after the clocks jump forward
(e.g. 2026-03-29 02:30 becomes 2026-03-29 03:30+02:00), instead of quietly
keeping an invalid local time.

Note that `aware_dt.astimezone(BERLIN)` alone is *not* enough to do this:
CPython's `datetime.astimezone` short-circuits and returns `self` unchanged
whenever the target tzinfo is already the same object as the source
tzinfo, so no recomputation happens. Normalising therefore requires
routing through a third, different tzinfo (UTC) to force it.

Comparing two aware datetimes that share the same tzinfo object compares
their (year, month, ..., fold) fields directly and does *not* convert to
UTC first -- so two datetimes built along different paths (e.g. one from
`Commute.window_bounds` and one from `datetime.now(BERLIN)`) can compare
incorrectly around a DST transition even though both are "aware Berlin
datetimes". `next_window_start` therefore never compares Berlin-aware
datetimes directly; it always converts both sides to UTC first.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
_UTC = dt.timezone.utc

# Worst case: only one weekday is active and it fell yesterday, so the next
# occurrence is a full week out. Search "today" plus the next 7 days.
_MAX_SEARCH_DAYS = 8


def _normalise(value: dt.datetime) -> dt.datetime:
    """Resolve a Berlin-aware datetime to its real wall-clock representation.

    Round-trips through UTC so a nonexistent local time (spring gap) is
    rewritten to the equivalent valid wall clock, and an ambiguous local
    time (autumn fold) keeps the fold it was built with. See module
    docstring for why a direct `.astimezone(BERLIN)` cannot do this.
    """
    return value.astimezone(_UTC).astimezone(BERLIN)


@dataclass(frozen=True)
class Commute:
    """A saved route: origin/destination stop, lines ridden, weekdays and
    a Europe/Berlin departure window during which the user rides it."""

    origin_stop_id: str
    destination_stop_id: str
    lines: frozenset[str]
    weekdays: frozenset[int]  # Monday=0 .. Sunday=6
    window_start: dt.time
    window_end: dt.time
    delay_threshold_min: int = 5

    def __post_init__(self) -> None:
        object.__setattr__(self, "lines", frozenset(self.lines))
        object.__setattr__(self, "weekdays", frozenset(self.weekdays))
        if not self.lines:
            raise ValueError("lines must not be empty")
        if not self.weekdays:
            raise ValueError("weekdays must not be empty")
        if any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("weekdays must be within 0 (Monday) .. 6 (Sunday)")

    def is_active_on(self, date: dt.date) -> bool:
        """Whether this commute is ridden on the given calendar date."""
        return date.weekday() in self.weekdays

    def window_bounds(self, date: dt.date) -> tuple[dt.datetime, dt.datetime]:
        """The departure window on `date` as normalised Europe/Berlin datetimes."""
        start = _normalise(dt.datetime.combine(date, self.window_start, tzinfo=BERLIN))
        end = _normalise(dt.datetime.combine(date, self.window_end, tzinfo=BERLIN))
        return start, end

    def next_window_start(self, now: dt.datetime) -> dt.datetime:
        """The next departure window start at or after `now`.

        `now` must be timezone-aware (any timezone). The comparison is
        always done as UTC instants, never as Berlin wall clock -- see the
        module docstring for why that distinction matters around DST
        transitions.
        """
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        now_utc = now.astimezone(_UTC)
        start_date = now.astimezone(BERLIN).date()
        for offset in range(_MAX_SEARCH_DAYS):
            candidate_date = start_date + dt.timedelta(days=offset)
            if not self.is_active_on(candidate_date):
                continue
            start, _end = self.window_bounds(candidate_date)
            if start.astimezone(_UTC) >= now_utc:
                return start
        raise RuntimeError("no active weekday found in search window")

"""Deterministic disruption engine (charter G1).

Given a saved `Commute` and a HAFAS v6 `/stops/:id/departures` response,
decide whether the commute is affected right now and produce a short
German/English reason. Pure function, no I/O: `evaluate` only reads its
arguments. The caller fetches departures for the commute's origin stop;
only departures on one of the commute's lines and inside *today's* window
(`Commute.window_bounds`, DST-safe) count -- everything else is ignored, so
a disruption on another line never produces a false positive.

The departures' own `stop.id` is deliberately not compared with the
origin: HAFAS reports departures from a station's child stops under their
own ids (recorded 2026-09-30: Hauptbahnhof's regional platforms as
900003200 "[Gleis 1-8]", Alexanderplatz's trams and buses as 900100026
"Gontardstr." and others), so an exact match silently dropped them.

The delay threshold is a parameter of `evaluate`, independent of
`Commute.delay_threshold_min` (which nothing here reads). A delay counts
only when it is strictly greater than the threshold ("above").

Remark-based kinds (replacement service, construction, generic warning)
are only ever derived from remarks of type "warning" -- "hint" and
"status" remarks (e.g. "Additional service") never count, regardless of
their text.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
from dataclasses import dataclass
from typing import Any

from pendel.commute import BERLIN, Commute

DEFAULT_DELAY_THRESHOLD_MIN = 10

_DISRUPTIVE_REMARK_TYPES = {"warning"}

# Keyword match against a remark's code+summary+text, lowercased. Checked
# in this order: a remark mentioning "Ersatz" (Ersatzverkehr/Ersatzbus/
# Schienenersatzverkehr) is filed as replacement service even if it also
# mentions construction, since that is the more actionable fact for a
# rider. Anything else of type "warning" is a generic disruption/Störung.
# The API answers in English unless asked otherwise, and BVG's own remarks
# then read "Replacement service due to construction works", so both
# languages are matched.
_REPLACEMENT_SERVICE_KEYWORDS = ("ersatz", "replacement")
_CONSTRUCTION_KEYWORDS = ("bauarbeiten", "baustelle", "construction")
_TAG = re.compile(r"<[^>]+>")

# Deterministic severity order used both to pick the primary event (whose
# key becomes disruption_key) and to order Verdict.kinds.
_KIND_ORDER = ("cancellation", "replacement_service", "delay", "construction", "warning")

_REASON_DE = {
    "cancellation": "{line} fällt aus.",
    "replacement_service": "Ersatzverkehr auf {line}.",
    "delay": "{line} hat {minutes} Minuten Verspätung.",
    "construction": "Bauarbeiten auf {line}: {detail}",
    "warning": "Störung auf {line}: {detail}",
}
_REASON_EN = {
    "cancellation": "{line} is cancelled.",
    "replacement_service": "Replacement service on {line}.",
    "delay": "{line} is delayed by {minutes} minutes.",
    "construction": "Construction on {line}: {detail}",
    "warning": "Disruption on {line}: {detail}",
}

_UNAFFECTED_REASON_DE = "Keine Störung auf dieser Verbindung."
_UNAFFECTED_REASON_EN = "No disruption on this route."


@dataclass(frozen=True)
class Verdict:
    """`disruption_key` ids the single highest-priority event found (empty
    when `affected` is False), so a notifier can send once per disruption."""

    affected: bool
    kinds: list[str]
    reason_de: str
    reason_en: str
    disruption_key: str


@dataclass(frozen=True)
class _Event:
    kind: str
    key: str
    line: str
    minutes: int | None = None
    detail: str = ""


def _classify_remark(haystack: str) -> str:
    if any(keyword in haystack for keyword in _REPLACEMENT_SERVICE_KEYWORDS):
        return "replacement_service"
    if any(keyword in haystack for keyword in _CONSTRUCTION_KEYWORDS):
        return "construction"
    return "warning"


def _clean(value: str) -> str:
    """Remark texts carry HTML: entities ("Ostkreuz &#60;&#62; Lichtenberg")
    and links ("<a href=...>[MEHR/MORE]</a>"). Reasons are plain text."""
    return " ".join(html.unescape(_TAG.sub(" ", value)).split())


def _remark_key(remark: dict[str, Any], trip_id: str) -> str:
    """HAFAS remark codes are generic and shared across unrelated
    disruptions, so prefer the remark's own id, then the trip id, then a
    hash of its text, to keep disruption_keys distinct."""
    remark_id = remark.get("id")
    if remark_id:
        return str(remark_id)
    if trip_id:
        return trip_id
    text = remark.get("summary") or remark.get("text") or remark.get("code") or ""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _format_reason(event: _Event) -> tuple[str, str]:
    if event.kind == "delay":
        return (
            _REASON_DE[event.kind].format(line=event.line, minutes=event.minutes),
            _REASON_EN[event.kind].format(line=event.line, minutes=event.minutes),
        )
    if event.kind in ("construction", "warning"):
        return (
            _REASON_DE[event.kind].format(line=event.line, detail=event.detail),
            _REASON_EN[event.kind].format(line=event.line, detail=event.detail),
        )
    return (
        _REASON_DE[event.kind].format(line=event.line),
        _REASON_EN[event.kind].format(line=event.line),
    )


def evaluate(
    commute: Commute,
    departures_json: dict[str, Any],
    now: dt.datetime,
    *,
    delay_threshold_min: int = DEFAULT_DELAY_THRESHOLD_MIN,
) -> Verdict:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    date = now.astimezone(BERLIN).date()
    window_start, window_end = commute.window_bounds(date)

    events: list[_Event] = []
    for departure in departures_json.get("departures") or []:
        line = departure.get("line") or {}
        line_name = line.get("name")
        if line_name not in commute.lines:
            continue
        planned_when = departure.get("plannedWhen")
        if not planned_when:
            continue
        departure_time = dt.datetime.fromisoformat(planned_when)
        if not (window_start <= departure_time <= window_end):
            continue

        events.extend(_departure_disruption_events(departure, line_name, delay_threshold_min))

    if not events:
        return Verdict(
            affected=False,
            kinds=[],
            reason_de=_UNAFFECTED_REASON_DE,
            reason_en=_UNAFFECTED_REASON_EN,
            disruption_key="",
        )

    events.sort(key=lambda event: _KIND_ORDER.index(event.kind))
    kinds = list(dict.fromkeys(event.kind for event in events))
    primary = events[0]
    reason_de, reason_en = _format_reason(primary)
    return Verdict(
        affected=True,
        kinds=kinds,
        reason_de=reason_de,
        reason_en=reason_en,
        disruption_key=primary.key,
    )


def _departure_disruption_events(
    departure: dict[str, Any], line_name: str, delay_threshold_min: int
) -> list[_Event]:
    events: list[_Event] = []
    trip_id = departure.get("tripId") or ""

    if departure.get("cancelled"):
        events.append(_Event("cancellation", f"cancellation:{line_name}:{trip_id}", line_name))

    delay_seconds = departure.get("delay")
    if delay_seconds is not None and delay_seconds > delay_threshold_min * 60:
        minutes = delay_seconds // 60
        events.append(
            _Event("delay", f"delay:{line_name}:{trip_id}", line_name, minutes=minutes)
        )

    for remark in departure.get("remarks") or []:
        if remark.get("type") not in _DISRUPTIVE_REMARK_TYPES:
            continue
        haystack = " ".join(
            str(remark.get(field) or "") for field in ("code", "summary", "text")
        ).lower()
        kind = _classify_remark(haystack)
        detail = _clean(remark.get("summary") or remark.get("text") or remark.get("code") or "")
        key = f"{kind}:{line_name}:{_remark_key(remark, trip_id)}"
        events.append(_Event(kind, key, line_name, detail=detail))

    return events

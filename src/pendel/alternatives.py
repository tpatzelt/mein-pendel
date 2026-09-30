"""Suggest an alternative connection from a HAFAS /journeys-shaped response.

Pure function, deliberately independent of engine.py: it only reads the
Verdict fields the engine already exposes (affected, disruption_key) and the
Commute fields (lines, destination_stop_id). No live HAFAS calls are made
here; callers supply the /journeys response body.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from pendel.commute import BERLIN, Commute
from pendel.engine import Verdict


@dataclass(frozen=True)
class Alternative:
    line: str
    departure: dt.datetime
    arrival: dt.datetime
    destination_name: str
    summary_de: str
    summary_en: str


def suggest_alternative(
    commute: Commute, verdict: Verdict, journeys_json: dict[str, Any]
) -> Alternative | None:
    if not verdict.affected:
        return None

    affected_lines = _affected_lines(commute, verdict)

    best: tuple[dt.datetime, dict[str, Any], dict[str, Any]] | None = None
    for journey in journeys_json.get("journeys", []):
        candidate = _candidate(journey, commute, affected_lines)
        if candidate is None:
            continue
        if best is None or candidate[0] < best[0]:
            best = candidate

    if best is None:
        return None

    departure_utc, first_transit_leg, last_leg = best
    arrival_utc = _leg_time(last_leg, "arrival")
    line = first_transit_leg["line"]["name"]
    destination_name = last_leg["destination"]["name"]
    departure = departure_utc.astimezone(BERLIN)
    arrival = arrival_utc.astimezone(BERLIN)

    summary_de = f"{line} {departure:%H:%M} → {destination_name} an {arrival:%H:%M}"
    summary_en = f"{line} {departure:%H:%M} → {destination_name} arr {arrival:%H:%M}"

    return Alternative(
        line=line,
        departure=departure,
        arrival=arrival,
        destination_name=destination_name,
        summary_de=summary_de,
        summary_en=summary_en,
    )


def _candidate(
    journey: dict[str, Any], commute: Commute, affected_lines: set[str]
) -> tuple[dt.datetime, dict[str, Any], dict[str, Any]] | None:
    legs = journey.get("legs", [])
    if not legs:
        return None

    last_leg = legs[-1]
    destination = last_leg.get("destination", {})
    destination_station = destination.get("station") or {}
    if (
        destination.get("id") != commute.destination_stop_id
        and destination_station.get("id") != commute.destination_stop_id
    ):
        return None

    if any(leg.get("cancelled", False) for leg in legs):
        return None

    transit_legs = [leg for leg in legs if leg.get("line")]
    if not transit_legs:
        return None

    if any(leg["line"]["name"].lower() in affected_lines for leg in transit_legs):
        return None

    first_transit_leg = transit_legs[0]
    departure = _leg_time(first_transit_leg, "departure")
    return departure, first_transit_leg, last_leg


def _leg_time(leg: dict[str, Any], kind: str) -> dt.datetime:
    value = leg.get(kind) or leg[f"planned{kind.capitalize()}"]
    return dt.datetime.fromisoformat(value)


def _affected_lines(commute: Commute, verdict: Verdict) -> set[str]:
    lines_lower = {line.lower() for line in commute.lines}
    parts = verdict.disruption_key.split(":")
    if len(parts) >= 2 and parts[1].lower() in lines_lower:
        return {parts[1].lower()}
    return lines_lower

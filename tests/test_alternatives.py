import datetime as dt
import json
from pathlib import Path

from pendel.alternatives import suggest_alternative
from pendel.commute import BERLIN, Commute
from pendel.engine import Verdict

FIXTURES = Path(__file__).parent / "fixtures" / "hafas" / "alternatives"


def _commute(**overrides) -> Commute:
    defaults = dict(
        origin_stop_id="900000100001",
        destination_stop_id="900000200002",
        lines=frozenset({"S41", "U5"}),
        weekdays=frozenset({0, 1, 2, 3, 4}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
    )
    defaults.update(overrides)
    return Commute(**defaults)


def _verdict(**overrides) -> Verdict:
    defaults = dict(
        affected=True,
        kinds=["delay"],
        reason_de="S41 verspätet.",
        reason_en="S41 delayed.",
        disruption_key="delay:S41:900000100001",
    )
    defaults.update(overrides)
    return Verdict(**defaults)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_returns_none_when_verdict_is_not_affected():
    commute = _commute()
    verdict = _verdict(affected=False, kinds=[], reason_de="", reason_en="", disruption_key="")

    result = suggest_alternative(commute, verdict, _load("synthetic_alternative_found.json"))

    assert result is None


def test_earliest_unaffected_uncancelled_journey_wins():
    # The disruption is on S41. Candidates, earliest first:
    #   U5 07:30 direct   -> cancelled, must be skipped
    #   S41 07:35 direct  -> uses the affected line, must be skipped
    #   U2 07:41 direct   -> qualifies, must win
    #   walk + M1 (first transit leg 08:10) -> qualifies but later
    commute = _commute()
    verdict = _verdict()

    alt = suggest_alternative(commute, verdict, _load("synthetic_alternative_found.json"))

    assert alt is not None
    assert alt.line == "U2"
    assert alt.destination_name == "S Test West"
    assert alt.departure == dt.datetime(2026, 1, 5, 7, 41, tzinfo=BERLIN)
    assert alt.arrival == dt.datetime(2026, 1, 5, 7, 58, tzinfo=BERLIN)
    assert alt.summary_de == "U2 07:41 → S Test West an 07:58"
    assert alt.summary_en == "U2 07:41 → S Test West arr 07:58"


def test_none_when_every_journey_uses_the_affected_line():
    commute = _commute()
    verdict = _verdict()

    result = suggest_alternative(commute, verdict, _load("synthetic_all_affected.json"))

    assert result is None


def test_dst_spring_forward_summary_uses_local_berlin_time():
    # UTC instants around the 2026-03-29 transition: 00:45Z is 01:45 CET
    # (still +01:00), 01:10Z is 03:10 CEST (already +02:00). Asserting
    # against these local wall-clock values fails if the UTC->Berlin
    # conversion is dropped.
    commute = _commute()
    verdict = _verdict()

    alt = suggest_alternative(commute, verdict, _load("synthetic_dst_spring_forward.json"))

    assert alt is not None
    assert alt.departure == dt.datetime(2026, 3, 29, 1, 45, tzinfo=BERLIN)
    assert alt.departure.utcoffset() == dt.timedelta(hours=1)
    assert alt.arrival == dt.datetime(2026, 3, 29, 3, 10, tzinfo=BERLIN)
    assert alt.arrival.utcoffset() == dt.timedelta(hours=2)
    assert alt.summary_de == "U2 01:45 → S Test West an 03:10"


def test_realtime_departure_and_arrival_are_preferred_over_planned():
    commute = _commute()
    verdict = _verdict()

    alt = suggest_alternative(
        commute, verdict, _load("synthetic_realtime_departure_preferred.json")
    )

    assert alt is not None
    assert alt.departure == dt.datetime(2026, 1, 5, 7, 46, tzinfo=BERLIN)
    assert alt.arrival == dt.datetime(2026, 1, 5, 8, 1, tzinfo=BERLIN)


def test_alternative_found_when_arrival_is_at_child_stop_of_destination():
    # The saved destination is the parent station 900003201 (S+U Berlin
    # Hauptbahnhof). The only qualifying journey arrives at its child stop
    # 900003200 ("[Gleis 1-8]"), reported by HAFAS with a nested "station"
    # object naming the parent. A journey to an unrelated stop with no
    # matching station must still be rejected even though it departs first.
    commute = _commute(destination_stop_id="900003201")
    verdict = _verdict()

    alt = suggest_alternative(commute, verdict, _load("synthetic_child_stop_arrival.json"))

    assert alt is not None
    assert alt.line == "U2"
    assert alt.destination_name == "S+U Berlin Hauptbahnhof [Gleis 1-8]"
    assert alt.departure == dt.datetime(2026, 1, 5, 7, 41, tzinfo=BERLIN)
    assert alt.arrival == dt.datetime(2026, 1, 5, 7, 58, tzinfo=BERLIN)


def test_falls_back_to_all_commute_lines_when_disruption_key_line_is_not_ridden():
    # disruption_key names a line the commute does not ride at all (e.g. a
    # stop-level construction remark without a specific line). The
    # conservative fallback then treats every commute line as unsafe, so the
    # S41 journey is excluded and the U5-cancelled journey is excluded for
    # being cancelled, leaving U2 as the only valid pick.
    commute = _commute()
    verdict = _verdict(kinds=["construction"], disruption_key="construction:900000100001:x")

    alt = suggest_alternative(commute, verdict, _load("synthetic_alternative_found.json"))

    assert alt is not None
    assert alt.line == "U2"

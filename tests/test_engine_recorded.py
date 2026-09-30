"""G1 definition of done: the engine over *recorded* HAFAS responses.

Every file under fixtures/hafas/recorded/ is a real v6.bvg.transport.rest
/stops/:id/departures response, trimmed by scripts/record_fixtures.py (see
the README there for provenance). Offline: nothing here touches the network.
"""

import datetime as dt
import json
from pathlib import Path

import pytest

from pendel.commute import Commute
from pendel.engine import evaluate, line_choices, next_departures

RECORDED = Path(__file__).parent / "fixtures" / "hafas" / "recorded"
# Wednesday 2026-09-30, 07:00 Berlin: just before the recorded departures.
NOW = dt.datetime(2026, 9, 30, 5, 0, tzinfo=dt.timezone.utc)

ALEXANDERPLATZ = "900100003"
WESTKREUZ = "900024102"
HAUPTBAHNHOF = "900003201"
FRIEDRICHSTR = "900100001"
ZOO = "900023201"
OSTKREUZ = "900120003"


def _commute(origin: str, lines: set[str], start: tuple[int, int], end: tuple[int, int]):
    return Commute(
        origin_stop_id=origin,
        destination_stop_id="900000000",
        lines=frozenset(lines),
        weekdays=frozenset({0, 1, 2, 3, 4}),
        window_start=dt.time(*start),
        window_end=dt.time(*end),
    )


def _load(kind: str) -> dict:
    return json.loads((RECORDED / f"departures_{kind}.json").read_text())


@pytest.mark.parametrize(
    "fixture, origin, lines, window, expected_kinds, reason_de, reason_en",
    [
        (
            "undisturbed", ALEXANDERPLATZ, {"U2", "U8", "S5"}, ((7, 15), (8, 10)),
            [], "Keine Störung", "No disruption",
        ),
        (
            "cancellation", WESTKREUZ, {"S46"}, ((7, 15), (7, 25)),
            ["cancellation"], "S46 fällt aus.", "S46 is cancelled.",
        ),
        # Departs from Hauptbahnhof's child stop 900003200 "[Gleis 1-8]", not
        # the station id a visitor saves; the engine must still see it.
        (
            "delay", HAUPTBAHNHOF, {"ICE 644"}, ((6, 40), (6, 55)),
            ["delay"], "30 Minuten Verspätung", "delayed by 30 minutes",
        ),
        # BVG's remark is English ("Replacement Service") since the API
        # defaults to English.
        (
            "replacement_service", FRIEDRICHSTR, {"12"}, ((7, 10), (7, 30)),
            ["replacement_service"], "Ersatzverkehr auf 12", "Replacement service on 12",
        ),
        (
            "construction", ZOO, {"S5"}, ((7, 10), (7, 20)),
            ["construction"], "Bauarbeiten auf S5", "Construction on S5",
        ),
        (
            "warning", OSTKREUZ, {"RB32"}, ((7, 40), (7, 50)),
            ["warning"], "Störung auf RB32", "Disruption on RB32",
        ),
        # Partial cancellation with its own remark: both kinds, cancellation first.
        (
            "warning", OSTKREUZ, {"RB26"}, ((8, 0), (8, 10)),
            ["cancellation", "warning"], "RB26 fällt aus.", "RB26 is cancelled.",
        ),
    ],
)
def test_engine_over_recorded_fixtures(
    fixture, origin, lines, window, expected_kinds, reason_de, reason_en
):
    verdict = evaluate(_commute(origin, lines, *window), _load(fixture), NOW)

    assert verdict.affected == bool(expected_kinds)
    assert verdict.kinds == expected_kinds
    assert reason_de in verdict.reason_de
    assert reason_en in verdict.reason_en
    assert bool(verdict.disruption_key) == bool(expected_kinds)


@pytest.mark.parametrize(
    "fixture, origin, lines, window",
    [
        # Tram 12 has replacement service at Friedrichstr.; S-Bahn riders there don't.
        ("replacement_service", FRIEDRICHSTR, {"S1", "S2", "S25", "S3", "S5", "S7", "S9"},
         ((7, 10), (7, 30))),
        # S46 is cancelled at Westkreuz; a Ring (S41/S42) rider is not affected.
        ("cancellation", WESTKREUZ, {"S41", "S42"}, ((7, 15), (7, 25))),
    ],
)
def test_disruption_on_a_line_not_ridden_is_ignored(fixture, origin, lines, window):
    verdict = evaluate(_commute(origin, lines, *window), _load(fixture), NOW)

    assert not verdict.affected
    assert verdict.kinds == []


@pytest.mark.parametrize("typed", ["s5", " S 5 "])
def test_lines_typed_by_hand_still_match(typed):
    # The first real commute on the deployed app was saved as "s5".
    verdict = evaluate(_commute(ZOO, {typed}, (7, 10), (7, 20)), _load("construction"), NOW)

    assert verdict.kinds == ["construction"]


def test_remark_html_never_reaches_a_reason():
    # Ostkreuz's RB26 remark summary is "Teilausfall Ostkreuz &#60;&#62; Lichtenberg".
    departures = _load("warning")
    rb26 = [d for d in departures["departures"] if d["line"]["name"] == "RB26"]
    for departure in rb26:
        departure["cancelled"] = False
    verdict = evaluate(
        _commute(OSTKREUZ, {"RB26"}, (8, 0), (8, 10)), {"departures": rb26}, NOW
    )

    assert verdict.kinds == ["warning"]
    assert "Ostkreuz <> Lichtenberg" in verdict.reason_de
    assert "&#" not in verdict.reason_de + verdict.reason_en


def test_generic_remark_summary_falls_back_to_the_remark_text():
    # RB32's remark summary is the generic "Störung.", which by itself
    # tells a rider nothing; the reason must carry words from the
    # remark's own text ("Ausfall", "Oranienburg"), not just the label.
    verdict = evaluate(_commute(OSTKREUZ, {"RB32"}, (7, 40), (7, 50)), _load("warning"), NOW)

    assert verdict.kinds == ["warning"]
    assert "Ausfall" in verdict.reason_de
    assert "Oranienburg" in verdict.reason_de
    assert verdict.reason_de != "Störung auf RB32: Störung."


def test_line_choices_from_recorded_departures():
    # Alexanderplatz 900100003: every line actually departing there, for the
    # setup checkboxes -- a visitor never types a line name (charter G1).
    choices = line_choices(_load("undisturbed"))

    assert choices == [
        "100", "200", "248", "300", "M2", "M4", "M5", "M6",
        "S3", "S5", "S7", "S9", "U2", "U5", "U8",
    ]
    assert len(choices) == len(set(choices))


def test_engine_evaluates_recorded_platform_fixture():
    # platforms_ostkreuz.json is trimmed to also keep platform/plannedPlatform
    # (charter G3); the engine ignores those fields but must not choke on them.
    departures = json.loads((RECORDED / "platforms_ostkreuz.json").read_text())

    verdict = evaluate(_commute(OSTKREUZ, {"RB32", "S41", "S7"}, (17, 15), (17, 25)), departures, NOW)

    assert verdict is not None


NOW_PLATFORMS = dt.datetime(2026, 9, 30, 15, 15, tzinfo=dt.timezone.utc)  # 17:15 Berlin


def test_next_departures_over_recorded_platform_fixture():
    # platforms_ostkreuz.json (charter G3): planned vs real-time time and
    # platform for the next departures on the ridden lines, at or after now.
    departures = json.loads((RECORDED / "platforms_ostkreuz.json").read_text())
    commute = _commute(OSTKREUZ, {"RB32", "S41", "S7"}, (17, 15), (17, 25))

    result = next_departures(commute, departures, NOW_PLATFORMS)

    assert [d.line for d in result] == ["RB32", "S41", "S7"]
    rb32, s41, s7 = result
    assert rb32.planned == dt.datetime.fromisoformat("2026-09-30T17:18:00+02:00")
    assert rb32.realtime == dt.datetime.fromisoformat("2026-09-30T17:20:00+02:00")
    assert rb32.delay_min == 2
    assert rb32.planned_platform == "14"
    assert rb32.platform == "14"
    assert rb32.cancelled is False
    assert s41.planned == s41.realtime == dt.datetime.fromisoformat("2026-09-30T17:20:00+02:00")
    assert s41.delay_min == 0
    assert s7.planned == s7.realtime == dt.datetime.fromisoformat("2026-09-30T17:21:00+02:00")


def test_next_departures_includes_a_delayed_departure_whose_planned_time_has_passed():
    # M43 is planned for 17:13 with real-time 17:33: still to come at 17:15,
    # so it must appear, sorted by planned time (not real time).
    departures = json.loads((RECORDED / "platforms_ostkreuz.json").read_text())
    commute = _commute(OSTKREUZ, {"M43"}, (17, 0), (17, 30))

    result = next_departures(commute, departures, NOW_PLATFORMS)

    assert [d.planned.isoformat() for d in result] == [
        "2026-09-30T17:13:00+02:00",
        "2026-09-30T17:16:00+02:00",
    ]
    assert result[0].realtime == dt.datetime.fromisoformat("2026-09-30T17:33:00+02:00")
    assert result[0].delay_min == 20
    assert result[1].delay_min == 16


def test_next_departures_respects_limit():
    departures = json.loads((RECORDED / "platforms_ostkreuz.json").read_text())
    commute = _commute(OSTKREUZ, {"RB32", "S41", "S7"}, (17, 15), (17, 25))

    result = next_departures(commute, departures, NOW_PLATFORMS, limit=2)

    assert [d.line for d in result] == ["RB32", "S41"]


def test_every_recorded_fixture_is_documented():
    readme = (RECORDED / "README.md").read_text()
    for path in RECORDED.glob("*.json"):
        assert path.name in readme

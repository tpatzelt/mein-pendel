import dataclasses
import datetime as dt
import json
from pathlib import Path

import pytest

from pendel.commute import Commute
from pendel.engine import evaluate, line_choices, next_departures

UTC = dt.timezone.utc
FIXTURES = Path(__file__).parent / "fixtures" / "hafas" / "engine"
ORIGIN_STOP_ID = "900000100001"


def _commute(**overrides) -> Commute:
    defaults = dict(
        origin_stop_id=ORIGIN_STOP_ID,
        destination_stop_id="900000200002",
        lines=frozenset({"S41"}),
        weekdays=frozenset({0, 1, 2, 3, 4}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
    )
    defaults.update(overrides)
    return Commute(**defaults)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _departures(**departure_overrides) -> dict:
    departure = {
        "tripId": "1|9999|0|1|5012026",
        "stop": {"id": ORIGIN_STOP_ID, "name": "S Test Ost"},
        "when": "2026-01-05T07:45:00+01:00",
        "plannedWhen": "2026-01-05T07:45:00+01:00",
        "delay": 0,
        "cancelled": False,
        "line": {"id": "line:S41", "name": "S41", "product": "suburban"},
        "remarks": [],
    }
    departure.update(departure_overrides)
    return {"departures": [departure]}


NOW_WINTER = dt.datetime(2026, 1, 5, 6, 0, tzinfo=UTC)  # Monday, same Berlin date as the fixtures


@pytest.mark.parametrize(
    "fixture_name, expected_kinds, reason_de_substring, reason_en_substring",
    [
        ("synthetic_undisturbed.json", [], "Keine Störung", "No disruption"),
        ("synthetic_cancellation.json", ["cancellation"], "fällt aus", "is cancelled"),
        (
            "synthetic_replacement_service.json",
            ["replacement_service"],
            "Ersatzverkehr",
            "Replacement service",
        ),
        ("synthetic_delay.json", ["delay"], "Verspätung", "delayed by"),
        ("synthetic_construction.json", ["construction"], "Bauarbeiten", "Construction"),
        ("synthetic_warning.json", ["warning"], "Störung auf", "Disruption on"),
    ],
)
def test_engine_over_synthetic_fixtures(
    fixture_name, expected_kinds, reason_de_substring, reason_en_substring
):
    commute = _commute()
    verdict = evaluate(commute, _load(fixture_name), NOW_WINTER)

    assert verdict.affected == bool(expected_kinds)
    assert verdict.kinds == expected_kinds
    assert reason_de_substring in verdict.reason_de
    assert reason_en_substring in verdict.reason_en
    if expected_kinds:
        assert verdict.disruption_key
    else:
        assert verdict.disruption_key == ""


EXPECTED_PLANNED_WINTER = dt.datetime.fromisoformat("2026-01-05T07:45:00+01:00")


@pytest.mark.parametrize(
    "fixture_name",
    ["synthetic_cancellation.json", "synthetic_delay.json"],
)
def test_verdict_carries_the_disrupted_line_and_its_planned_time(fixture_name):
    commute = _commute()
    verdict = evaluate(commute, _load(fixture_name), NOW_WINTER)

    assert verdict.line == "S41"
    assert verdict.planned == EXPECTED_PLANNED_WINTER


def test_a_remark_only_verdict_also_carries_line_and_planned_time():
    # synthetic_warning.json has no cancellation or delay, only a
    # disruptive remark -- G5 still needs the line and planned time.
    commute = _commute()
    verdict = evaluate(commute, _load("synthetic_warning.json"), NOW_WINTER)

    assert verdict.kinds == ["warning"]
    assert verdict.line == "S41"
    assert verdict.planned == EXPECTED_PLANNED_WINTER


def test_unaffected_verdict_has_no_line_or_planned_time():
    commute = _commute()
    verdict = evaluate(commute, _load("synthetic_undisturbed.json"), NOW_WINTER)

    assert verdict.affected is False
    assert verdict.line == ""
    assert verdict.planned is None


def test_replace_for_the_alternative_suffix_keeps_line_and_planned():
    # scheduler.py appends the alternative summary via
    # dataclasses.replace(verdict, reason_de=..., reason_en=...); that must
    # not lose the line/planned fields a notifier needs (charter G5).
    commute = _commute()
    verdict = evaluate(commute, _load("synthetic_cancellation.json"), NOW_WINTER)

    replaced = dataclasses.replace(verdict, reason_de="x")

    assert replaced.line == verdict.line == "S41"
    assert replaced.planned == verdict.planned == EXPECTED_PLANNED_WINTER


def test_disruption_on_line_not_ridden_is_not_a_false_positive():
    commute = _commute()
    verdict = evaluate(commute, _load("synthetic_unridden_line.json"), NOW_WINTER)

    assert verdict.affected is False
    assert verdict.kinds == []
    assert verdict.disruption_key == ""


def test_hint_and_additional_service_status_remarks_are_ignored_even_with_trigger_words():
    commute = _commute()
    verdict = evaluate(commute, _load("synthetic_hint_and_status_ignored.json"), NOW_WINTER)

    assert verdict.affected is False
    assert verdict.kinds == []


def test_missing_remarks_key_is_tolerated():
    commute = _commute()
    verdict = evaluate(commute, _load("synthetic_no_remarks_key.json"), NOW_WINTER)

    assert verdict.affected is False


def test_delay_one_second_under_default_threshold_is_not_affected():
    commute = _commute()
    verdict = evaluate(commute, _departures(delay=599), NOW_WINTER)

    assert verdict.affected is False


def test_delay_exactly_at_default_threshold_is_not_affected():
    commute = _commute()
    verdict = evaluate(commute, _departures(delay=600), NOW_WINTER)

    assert verdict.affected is False


def test_delay_one_second_over_default_threshold_is_affected():
    commute = _commute()
    verdict = evaluate(commute, _departures(delay=601), NOW_WINTER)

    assert verdict.affected is True
    assert verdict.kinds == ["delay"]


def test_delay_threshold_is_a_parameter_independent_of_commute_field():
    # Commute.delay_threshold_min defaults to 5, but evaluate() must not
    # use it: a 6-minute delay must not count against the (default 10 min)
    # threshold passed to evaluate().
    commute = _commute()
    assert commute.delay_threshold_min == 5
    verdict = evaluate(commute, _departures(delay=360), NOW_WINTER)  # 6 minutes

    assert verdict.affected is False


def test_delay_threshold_min_keyword_overrides_default():
    commute = _commute()
    verdict = evaluate(commute, _departures(delay=360), NOW_WINTER, delay_threshold_min=5)

    assert verdict.affected is True
    assert verdict.kinds == ["delay"]


def test_now_must_be_timezone_aware():
    commute = _commute()
    with pytest.raises(ValueError):
        evaluate(commute, _departures(), dt.datetime(2026, 1, 5, 6, 0))


def test_dst_spring_forward_window_places_departures_correctly():
    # 2026-03-29: wall-clock 02:00-03:00 does not exist, so the configured
    # 02:30-02:45 window normalises to 03:30-03:45 CEST. The fixture has a
    # warning inside that real window and a cancellation 5 min after it;
    # a naive window would wrongly admit the cancellation (higher
    # priority) or match neither departure.
    commute = _commute(
        weekdays=frozenset({6}), window_start=dt.time(2, 30), window_end=dt.time(2, 45)
    )
    now = dt.datetime(2026, 3, 29, 0, 0, tzinfo=UTC)

    verdict = evaluate(commute, _load("synthetic_dst_spring_forward.json"), now)

    assert verdict.affected is True
    assert verdict.kinds == ["warning"]


def test_evaluate_uses_the_window_that_started_yesterday_when_it_crosses_midnight():
    # Window 23:30-00:30 starting Monday: a cancellation at 00:10 Tuesday
    # belongs to the window that started Monday evening, not to a window
    # starting fresh on Tuesday (which `window_bounds(today)` alone would
    # wrongly compute, and whose weekday isn't even active here). Checked
    # once while still Monday evening and once after midnight -- both fall
    # inside that same window.
    commute = _commute(
        weekdays=frozenset({0}), window_start=dt.time(23, 30), window_end=dt.time(0, 30)
    )
    departures = _departures(
        plannedWhen="2026-01-06T00:10:00+01:00",
        when="2026-01-06T00:10:00+01:00",
        cancelled=True,
        delay=None,
    )

    before_midnight = evaluate(commute, departures, dt.datetime(2026, 1, 5, 22, 50, tzinfo=UTC))
    after_midnight = evaluate(commute, departures, dt.datetime(2026, 1, 5, 23, 5, tzinfo=UTC))

    for verdict in (before_midnight, after_midnight):
        assert verdict.affected is True
        assert verdict.kinds == ["cancellation"]


def test_evaluate_window_crossing_midnight_on_dst_fallback_night():
    # 2026-10-25 is the autumn DST fall-back night (clocks move from CEST
    # to CET at 03:00 local). A window starting the evening before
    # (Saturday 23:30-00:30) must still be found via window_containing
    # just after midnight, before the fold itself happens.
    commute = _commute(
        weekdays=frozenset({5}), window_start=dt.time(23, 30), window_end=dt.time(0, 30)
    )
    departures = _departures(
        plannedWhen="2026-10-25T00:10:00+02:00",
        when="2026-10-25T00:10:00+02:00",
        cancelled=True,
        delay=None,
    )

    verdict = evaluate(commute, departures, dt.datetime(2026, 10, 24, 22, 5, tzinfo=UTC))

    assert verdict.affected is True
    assert verdict.kinds == ["cancellation"]


def test_kinds_are_ordered_deterministically_and_key_picks_highest_priority():
    departures = _departures(cancelled=True, delay=900)

    commute = _commute()
    verdict = evaluate(commute, departures, NOW_WINTER)

    assert verdict.kinds == ["cancellation", "delay"]
    assert verdict.disruption_key.startswith("cancellation:")


def test_disruption_key_distinguishes_different_warning_remarks():
    commute = _commute()
    first = evaluate(
        commute,
        _departures(
            tripId="1|1|0|1|5012026",
            remarks=[
                {
                    "type": "warning",
                    "code": "text.realtime.journey.disruption",
                    "summary": "Signalstörung",
                    "text": "Signalstörung A.",
                }
            ],
        ),
        NOW_WINTER,
    )
    second = evaluate(
        commute,
        _departures(
            tripId="1|2|0|1|5012026",
            remarks=[
                {
                    "type": "warning",
                    "code": "text.realtime.journey.disruption",
                    "summary": "Signalstörung",
                    "text": "Signalstörung B.",
                }
            ],
        ),
        NOW_WINTER,
    )

    assert first.disruption_key != second.disruption_key


def test_generic_summary_falls_back_to_shortened_cleaned_text():
    # "Störung." is one of the generic category labels HAFAS reuses across
    # unrelated disruptions; the reason must use the remark's own text
    # instead, cut to 120 chars at a word boundary.
    long_text = (
        "Wegen einer Signalstörung zwischen Ostbahnhof und Ostkreuz verkehrt die Linie "
        "heute nur eingeschränkt. Nutzen Sie bitte die S-Bahn als Ausweichmöglichkeit "
        "bis auf Weiteres."
    )
    commute = _commute()
    verdict = evaluate(
        commute,
        _departures(
            remarks=[{"type": "warning", "summary": "Störung.", "text": long_text}]
        ),
        NOW_WINTER,
    )

    assert "Signalstörung" in verdict.reason_de
    assert "Störung auf" in verdict.reason_de  # kind label, not the raw summary detail
    assert verdict.reason_de.endswith("…")
    detail = verdict.reason_de.split(": ", 1)[1]
    assert len(detail) <= 121  # 120 chars plus the trailing ellipsis


def test_generic_summary_with_empty_text_keeps_the_summary():
    commute = _commute()
    verdict = evaluate(
        commute,
        _departures(remarks=[{"type": "warning", "summary": "Information.", "text": ""}]),
        NOW_WINTER,
    )

    assert "Information." in verdict.reason_de
    assert "Information." in verdict.reason_en


def test_non_generic_summary_is_unchanged():
    commute = _commute()
    verdict = evaluate(
        commute,
        _departures(
            remarks=[
                {
                    "type": "warning",
                    "summary": "Signalstörung bei Ostkreuz",
                    "text": "Ein sehr viel längerer Text, der nicht verwendet werden sollte.",
                }
            ]
        ),
        NOW_WINTER,
    )

    assert "Signalstörung bei Ostkreuz" in verdict.reason_de
    assert "sehr viel längerer Text" not in verdict.reason_de


def test_generic_summary_link_residue_is_dropped():
    commute = _commute()
    verdict = evaluate(
        commute,
        _departures(
            remarks=[
                {
                    "type": "warning",
                    "summary": "Hinweis",
                    "text": (
                        'Kurzer Hinweistext. <a href="https://example.invalid">'
                        "[MEHR/MORE]</a>"
                    ),
                }
            ]
        ),
        NOW_WINTER,
    )

    assert "Kurzer Hinweistext." in verdict.reason_de
    assert "MEHR" not in verdict.reason_de
    assert "MORE" not in verdict.reason_de


def test_line_choices_is_empty_for_no_departures():
    assert line_choices({"departures": []}) == []
    assert line_choices({}) == []


def test_line_choices_skips_a_departure_without_a_line_name():
    departures = {
        "departures": [
            {"line": {"product": "bus"}},
            {"line": {"name": "S41", "product": "suburban"}},
            {},
        ]
    }

    assert line_choices(departures) == ["S41"]


def test_line_choices_deduplicates_by_normalised_line_name():
    departures = {
        "departures": [
            {"line": {"name": "S41"}},
            {"line": {"name": " s 41 "}},
            {"line": {"name": "S9"}},
        ]
    }

    assert line_choices(departures) == ["S41", "S9"]


def _departure(
    line: str,
    planned: str,
    when: str | None = None,
    *,
    delay: int | None = None,
    cancelled: bool = False,
    platform: str | None = None,
    planned_platform: str | None = None,
) -> dict:
    return {
        "tripId": f"1|{line}|0|1|5012026",
        "stop": {"id": ORIGIN_STOP_ID, "name": "S Test Ost"},
        "when": when,
        "plannedWhen": planned,
        "delay": delay,
        "cancelled": cancelled,
        "platform": platform,
        "plannedPlatform": planned_platform,
        "line": {"id": f"line:{line}", "name": line, "product": "suburban"},
        "remarks": [],
    }


def test_next_departures_filters_to_ridden_lines_sorted_by_planned_time():
    commute = _commute(lines=frozenset({"S41", "S9"}))
    departures = {
        "departures": [
            _departure("S9", "2026-01-05T07:50:00+01:00", "2026-01-05T07:50:00+01:00"),
            _departure("S41", "2026-01-05T07:45:00+01:00", "2026-01-05T07:45:00+01:00"),
            _departure("S42", "2026-01-05T07:40:00+01:00", "2026-01-05T07:40:00+01:00"),
        ]
    }

    result = next_departures(commute, departures, NOW_WINTER)

    assert [d.line for d in result] == ["S41", "S9"]


def test_next_departures_respects_limit():
    commute = _commute(lines=frozenset({"S41", "S9"}))
    departures = {
        "departures": [
            _departure("S9", "2026-01-05T07:50:00+01:00", "2026-01-05T07:50:00+01:00"),
            _departure("S41", "2026-01-05T07:45:00+01:00", "2026-01-05T07:45:00+01:00"),
        ]
    }

    result = next_departures(commute, departures, NOW_WINTER, limit=1)

    assert [d.line for d in result] == ["S41"]


def test_next_departures_includes_a_still_pending_delayed_departure_and_excludes_a_left_one():
    # NOW_WINTER is 07:00 Berlin. A departure planned before now but running
    # late enough that its real-time is still ahead must be included (the
    # rider can still catch it); one whose real-time has also passed must
    # not appear, even though both were "planned" before now.
    commute = _commute(lines=frozenset({"S41"}))
    departures = {
        "departures": [
            _departure(
                "S41", "2026-01-05T06:45:00+01:00", "2026-01-05T07:10:00+01:00", delay=1500
            ),
            _departure(
                "S41", "2026-01-05T06:40:00+01:00", "2026-01-05T06:50:00+01:00", delay=600
            ),
        ]
    }

    result = next_departures(commute, departures, NOW_WINTER)

    assert [d.planned.isoformat() for d in result] == ["2026-01-05T06:45:00+01:00"]
    assert result[0].realtime == dt.datetime.fromisoformat("2026-01-05T07:10:00+01:00")


def test_next_departures_uses_planned_when_as_the_departure_time_for_a_cancellation():
    # HAFAS never reports `when` for a cancelled departure; the fallback to
    # `plannedWhen` must still decide in/out correctly on both sides.
    commute = _commute(lines=frozenset({"S41"}))
    departures = {
        "departures": [
            _departure("S41", "2026-01-05T07:10:00+01:00", cancelled=True),
            _departure("S41", "2026-01-05T06:50:00+01:00", cancelled=True),
        ]
    }

    result = next_departures(commute, departures, NOW_WINTER)

    assert len(result) == 1
    assert result[0].planned == dt.datetime.fromisoformat("2026-01-05T07:10:00+01:00")
    assert result[0].realtime is None
    assert result[0].cancelled is True


def test_next_departures_delay_min_truncates_toward_zero():
    commute = _commute(lines=frozenset({"S41"}))
    departures = {
        "departures": [
            _departure(
                "S41", "2026-01-05T07:10:00+01:00", "2026-01-05T07:09:30+01:00", delay=-30
            ),
        ]
    }

    result = next_departures(commute, departures, NOW_WINTER)

    assert result[0].delay_min == 0


def test_next_departures_carries_platform_fields():
    commute = _commute(lines=frozenset({"S41"}))
    departures = {
        "departures": [
            _departure(
                "S41",
                "2026-01-05T07:10:00+01:00",
                "2026-01-05T07:10:00+01:00",
                platform="4",
                planned_platform="3",
            ),
        ]
    }

    result = next_departures(commute, departures, NOW_WINTER)

    assert result[0].platform == "4"
    assert result[0].planned_platform == "3"


def test_next_departures_now_must_be_timezone_aware():
    commute = _commute(lines=frozenset({"S41"}))
    with pytest.raises(ValueError):
        next_departures(commute, _departures(), dt.datetime(2026, 1, 5, 6, 0))

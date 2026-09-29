import datetime as dt

import pytest

from pendel.commute import BERLIN, Commute

UTC = dt.timezone.utc


def _commute(**overrides) -> Commute:
    defaults = dict(
        origin_stop_id="900000100001",
        destination_stop_id="900000200002",
        lines=frozenset({"S41"}),
        weekdays=frozenset({0, 1, 2, 3, 4}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
    )
    defaults.update(overrides)
    return Commute(**defaults)


def test_frozen_dataclass_rejects_mutation():
    commute = _commute()
    with pytest.raises(AttributeError):
        commute.delay_threshold_min = 10


def test_delay_threshold_defaults_to_five_minutes():
    assert _commute().delay_threshold_min == 5


def test_lines_and_weekdays_are_coerced_to_frozensets():
    commute = _commute(lines={"s41", "S41"}, weekdays=[0, 1, 1, 2])
    assert commute.lines == frozenset({"s41", "S41"})
    assert commute.weekdays == frozenset({0, 1, 2})


def test_rejects_empty_lines():
    with pytest.raises(ValueError):
        _commute(lines=frozenset())


def test_rejects_empty_weekdays():
    with pytest.raises(ValueError):
        _commute(weekdays=frozenset())


def test_rejects_out_of_range_weekday():
    with pytest.raises(ValueError):
        _commute(weekdays=frozenset({7}))


def test_is_active_on_matches_configured_weekdays():
    commute = _commute(weekdays=frozenset({0, 2}))  # Monday, Wednesday
    assert commute.is_active_on(dt.date(2026, 1, 5))  # Monday
    assert not commute.is_active_on(dt.date(2026, 1, 6))  # Tuesday
    assert commute.is_active_on(dt.date(2026, 1, 7))  # Wednesday


def test_window_bounds_on_ordinary_winter_day():
    commute = _commute()
    start, end = commute.window_bounds(dt.date(2026, 1, 5))
    assert start == dt.datetime(2026, 1, 5, 7, 30, tzinfo=BERLIN)
    assert start.utcoffset() == dt.timedelta(hours=1)
    assert end == dt.datetime(2026, 1, 5, 8, 0, tzinfo=BERLIN)
    assert start.astimezone(UTC) == dt.datetime(2026, 1, 5, 6, 30, tzinfo=UTC)


def test_window_bounds_on_ordinary_summer_day():
    commute = _commute()
    start, _end = commute.window_bounds(dt.date(2026, 7, 6))  # Monday, CEST
    assert start.utcoffset() == dt.timedelta(hours=2)
    assert start.astimezone(UTC) == dt.datetime(2026, 7, 6, 5, 30, tzinfo=UTC)


def test_window_bounds_spring_forward_gap_is_normalised_forward():
    # 2026-03-29 is a Sunday; wall-clock 02:00-03:00 does not exist.
    commute = _commute(weekdays=frozenset({6}), window_start=dt.time(2, 30), window_end=dt.time(2, 45))
    start, end = commute.window_bounds(dt.date(2026, 3, 29))
    assert start == dt.datetime(2026, 3, 29, 3, 30, tzinfo=BERLIN)
    assert start.utcoffset() == dt.timedelta(hours=2)
    assert start.astimezone(UTC) == dt.datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    assert end == dt.datetime(2026, 3, 29, 3, 45, tzinfo=BERLIN)


def test_window_bounds_fall_back_repeated_hour_uses_first_occurrence():
    # 2026-10-25 is a Sunday; wall-clock 02:00-03:00 happens twice.
    commute = _commute(weekdays=frozenset({6}), window_start=dt.time(2, 30), window_end=dt.time(3, 30))
    start, end = commute.window_bounds(dt.date(2026, 10, 25))
    assert start.fold == 0
    assert start.utcoffset() == dt.timedelta(hours=2)  # first (CEST) occurrence
    assert start.astimezone(UTC) == dt.datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert end.utcoffset() == dt.timedelta(hours=1)  # 03:30 only exists as CET


def test_next_window_start_rejects_naive_now():
    commute = _commute()
    with pytest.raises(ValueError):
        commute.next_window_start(dt.datetime(2026, 1, 5, 6, 0))


def test_next_window_start_plain_upcoming_today():
    commute = _commute(weekdays=frozenset({0}))  # Monday only
    now = dt.datetime(2026, 1, 5, 6, 0, tzinfo=UTC)  # Monday, before window
    start = commute.next_window_start(now)
    assert start.astimezone(UTC) == dt.datetime(2026, 1, 5, 6, 30, tzinfo=UTC)


def test_next_window_start_skips_to_next_week_when_today_passed():
    commute = _commute(weekdays=frozenset({0}))  # Monday only
    now = dt.datetime(2026, 1, 5, 12, 0, tzinfo=UTC)  # Monday, after window
    start = commute.next_window_start(now)
    assert start.date() == dt.date(2026, 1, 12)


def test_next_window_start_spring_forward_gap_window_still_upcoming():
    commute = _commute(weekdays=frozenset({6}), window_start=dt.time(2, 30), window_end=dt.time(2, 45))
    now = dt.datetime(2026, 3, 29, 1, 10, tzinfo=UTC)
    start = commute.next_window_start(now)
    assert start.astimezone(UTC) == dt.datetime(2026, 3, 29, 1, 30, tzinfo=UTC)


def test_next_window_start_spring_forward_gap_window_already_passed():
    commute = _commute(weekdays=frozenset({6}), window_start=dt.time(2, 30), window_end=dt.time(2, 45))
    now = dt.datetime(2026, 3, 29, 1, 40, tzinfo=UTC)
    start = commute.next_window_start(now)
    assert start.date() == dt.date(2026, 4, 5)


def test_next_window_start_spring_forward_gap_berlin_aware_now():
    # Same instant as the "still upcoming" case above, but `now` is
    # Berlin-aware instead of UTC-aware -- must give the same answer.
    commute = _commute(weekdays=frozenset({6}), window_start=dt.time(2, 30), window_end=dt.time(2, 45))
    now = dt.datetime(2026, 3, 29, 1, 10, tzinfo=UTC).astimezone(BERLIN)
    start = commute.next_window_start(now)
    assert start.astimezone(UTC) == dt.datetime(2026, 3, 29, 1, 30, tzinfo=UTC)


def test_next_window_start_fall_back_still_upcoming_asserts_utc_instant():
    commute = _commute(weekdays=frozenset({6}), window_start=dt.time(2, 30), window_end=dt.time(3, 30))
    now = dt.datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
    start = commute.next_window_start(now)
    assert start.date() == dt.date(2026, 10, 25)
    assert start.astimezone(UTC) == dt.datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


def test_next_window_start_fall_back_berlin_aware_now_in_second_occurrence():
    # `now` falls in the *second* (CET, fold=1) occurrence of 02:00-03:00,
    # 45 minutes after the window's first-occurrence UTC instant. A naive
    # wall-clock comparison (02:15 < 02:30) would wrongly think the window
    # is still upcoming; comparing UTC instants correctly rolls to next week.
    commute = _commute(weekdays=frozenset({6}), window_start=dt.time(2, 30), window_end=dt.time(3, 30))
    now = dt.datetime(2026, 10, 25, 1, 15, tzinfo=UTC).astimezone(BERLIN)
    assert now.fold == 1
    start = commute.next_window_start(now)
    assert start.date() == dt.date(2026, 11, 1)

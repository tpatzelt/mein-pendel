"""Scheduler tests (charter G3): a simulated morning drives ticks across
fixtures and asserts exactly one notification per disruption per commute,
none when unaffected, and a resolved message once a disruption clears.

Also covers the two things the parked T-0009 attempts were sent back for:
one departures() request per origin stop per tick even with differing
commute windows, and error isolation (one stop's HafasError, one commute's
evaluate/tracker/channel failure, one failing tick) never stopping the rest.

Fixtures under tests/fixtures/hafas/scheduler/ are hand-made, labelled
synthetic HAFAS v6 responses; nothing here makes or requires a live call.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from pendel import db
from pendel.commute import BERLIN
from pendel.hafas import HafasClient
from pendel.notify import FakeChannel
from pendel.scheduler import Scheduler

FIXTURES = Path(__file__).parent / "fixtures" / "hafas" / "scheduler"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDEL_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def conn(data_dir):
    connection = db.connect()
    yield connection
    connection.close()


def _insert_commute(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    origin_stop_id: str = "900000100001",
    destination_stop_id: str = "900000200002",
    lines: str = "S41",
    weekdays: str = "0,1,2,3,4",
    window_start: str = "06:00",
    window_end: str = "08:00",
    delay_threshold_min: int = 5,
) -> int:
    cur = conn.execute(
        "INSERT INTO commutes (user_id, origin_stop_id, destination_stop_id, "
        "lines, weekdays, window_start, window_end, delay_threshold_min) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            origin_stop_id,
            destination_stop_id,
            lines,
            weekdays,
            window_start,
            window_end,
            delay_threshold_min,
        ),
    )
    conn.commit()
    return cur.lastrowid


def _insert_channel(conn: sqlite3.Connection, user_id: str, kind: str, target: str) -> None:
    conn.execute(
        "INSERT INTO channels (user_id, kind, target) VALUES (?, ?, ?)",
        (user_id, kind, target),
    )
    conn.commit()


def _notification_count(conn: sqlite3.Connection, commute_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE commute_id = ?", (commute_id,)
    ).fetchone()[0]


def _berlin(hour: int, minute: int) -> dt.datetime:
    return dt.datetime(2026, 1, 5, hour, minute, tzinfo=BERLIN)  # Monday, winter (+01:00)


def _hafas(handler) -> HafasClient:
    return HafasClient(httpx.Client(transport=httpx.MockTransport(handler)))


def test_simulated_morning_one_activation_one_resolved_and_unaffected_line_gets_nothing(conn):
    user_id = db.create_user(conn)
    affected_id = _insert_commute(conn, user_id, lines="S41")
    unaffected_id = _insert_commute(conn, user_id, lines="U5")
    _insert_channel(conn, user_id, "ntfy", "topic-abc")
    fake = FakeChannel()

    timeline = {
        "06:30": "synthetic_undisturbed.json",
        "06:45": "synthetic_ersatzverkehr.json",
        "07:00": "synthetic_ersatzverkehr.json",
        "07:15": "synthetic_undisturbed.json",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/journeys":
            return httpx.Response(200, json={"journeys": []})
        when = request.url.params["when"]
        hhmm = when[11:16]
        return httpx.Response(200, json=_load(timeline[hhmm]))

    sched = Scheduler(conn, _hafas(handler), {"ntfy": fake}, clock=lambda: None)

    for hhmm in ("06:30", "06:45", "07:00", "07:15"):
        hour, minute = (int(part) for part in hhmm.split(":"))
        sched.tick(_berlin(hour, minute))

    assert len(fake.sent) == 2
    activate_target, activate_text = fake.sent[0]
    resolved_target, resolved_text = fake.sent[1]
    assert activate_target == resolved_target == "topic-abc"
    assert "Ersatzverkehr" in activate_text
    assert "behoben" in resolved_text
    assert "resolved" in resolved_text
    assert _notification_count(conn, unaffected_id) == 0


def test_two_commutes_same_stop_different_windows_cause_a_single_departures_request(conn):
    user_id = db.create_user(conn)
    _insert_commute(conn, user_id, window_start="06:00", window_end="06:45")
    _insert_commute(conn, user_id, window_start="06:00", window_end="07:15")
    _insert_channel(conn, user_id, "ntfy", "topic-abc")
    fake = FakeChannel()

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_load("synthetic_undisturbed.json"))

    sched = Scheduler(conn, _hafas(handler), {"ntfy": fake}, clock=lambda: None)
    sched.tick(_berlin(6, 30))

    assert len(calls) == 1
    assert calls[0].url.params["duration"] == "45"


def test_hafas_error_on_one_stop_does_not_block_the_other_stop(conn):
    user_id = db.create_user(conn)
    broken_id = _insert_commute(conn, user_id, origin_stop_id="900000900009")
    healthy_id = _insert_commute(conn, user_id, origin_stop_id="900000100001")
    _insert_channel(conn, user_id, "ntfy", "topic-abc")
    fake = FakeChannel()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/journeys":
            return httpx.Response(200, json={"journeys": []})
        if "900000900009" in request.url.path:
            return httpx.Response(503)
        return httpx.Response(200, json=_load("synthetic_ersatzverkehr.json"))

    hafas = HafasClient(
        httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda seconds: None
    )
    sched = Scheduler(conn, hafas, {"ntfy": fake}, clock=lambda: None)
    sched.tick(_berlin(6, 30))

    assert len(fake.sent) == 1
    assert _notification_count(conn, broken_id) == 0
    assert _notification_count(conn, healthy_id) == 1


def test_alternative_summary_is_appended_to_the_notification_text(conn):
    user_id = db.create_user(conn)
    _insert_commute(conn, user_id, lines="S41,U5")
    _insert_channel(conn, user_id, "ntfy", "topic-abc")
    fake = FakeChannel()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/journeys":
            return httpx.Response(200, json=_load("synthetic_alternative.json"))
        return httpx.Response(200, json=_load("synthetic_ersatzverkehr.json"))

    sched = Scheduler(conn, _hafas(handler), {"ntfy": fake}, clock=lambda: None)
    sched.tick(_berlin(6, 30))

    assert len(fake.sent) == 1
    text = fake.sent[0][1]
    assert "U2" in text
    assert "07:41" in text


class _RaisingChannel:
    def send(self, target: str, text: str) -> None:
        raise RuntimeError("channel boom")


def test_one_commutes_channel_send_failure_does_not_block_another_commute(conn):
    user_a = db.create_user(conn)
    _insert_commute(conn, user_a, origin_stop_id="900000100001")
    _insert_channel(conn, user_a, "telegram", "chat-a")

    user_b = db.create_user(conn)
    _insert_commute(conn, user_b, origin_stop_id="900000100001")
    _insert_channel(conn, user_b, "ntfy", "topic-b")

    fake = FakeChannel()
    raising = _RaisingChannel()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/journeys":
            return httpx.Response(200, json={"journeys": []})
        return httpx.Response(200, json=_load("synthetic_ersatzverkehr.json"))

    sched = Scheduler(
        conn, _hafas(handler), {"telegram": raising, "ntfy": fake}, clock=lambda: None
    )
    sched.tick(_berlin(6, 30))

    assert len(fake.sent) == 1


class _Sentinel(Exception):
    pass


def test_run_forever_survives_a_failing_tick_and_keeps_going(conn):
    hafas = _hafas(lambda request: httpx.Response(200, json={"departures": []}))
    sched = Scheduler(
        conn,
        hafas,
        {},
        clock=lambda: _berlin(6, 30),
        sleep=_sleep_raising_after(3),
    )

    tick_calls: list[dt.datetime] = []
    real_tick = sched.tick

    def failing_first_then_real(now: dt.datetime) -> None:
        tick_calls.append(now)
        if len(tick_calls) == 1:
            raise RuntimeError("boom")
        real_tick(now)

    sched.tick = failing_first_then_real  # type: ignore[method-assign]

    with pytest.raises(_Sentinel):
        sched.run_forever()

    assert len(tick_calls) == 3


def _sleep_raising_after(n: int):
    calls: list[float] = []

    def sleep(seconds: float) -> None:
        calls.append(seconds)
        if len(calls) >= n:
            raise _Sentinel()

    return sleep


def test_invalid_lead_minutes_env_var_raises_at_construction(conn, monkeypatch):
    monkeypatch.setenv("PENDEL_CHECK_LEAD_MIN", "not-a-number")
    hafas = _hafas(lambda request: httpx.Response(200, json={"departures": []}))

    with pytest.raises(ValueError):
        Scheduler(conn, hafas, {}, clock=lambda: None)


def test_commute_due_at_lead_boundary_but_not_one_minute_earlier(conn):
    user_id = db.create_user(conn)
    _insert_commute(conn, user_id, window_start="08:00", window_end="08:30")
    _insert_channel(conn, user_id, "ntfy", "topic-abc")
    fake = FakeChannel()

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_load("synthetic_undisturbed.json"))

    sched = Scheduler(conn, _hafas(handler), {"ntfy": fake}, clock=lambda: None)

    sched.tick(_berlin(7, 29))  # 31 min before window_start: outside the 30 min lead
    assert calls == []

    sched.tick(_berlin(7, 30))  # exactly 30 min before window_start: at the lead boundary
    assert len(calls) == 1

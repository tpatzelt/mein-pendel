"""G5 definition-of-done test (charter): a scheduler tick drives a real
disruption fixture through to a FakeChannel message naming the commute,
line, planned time, reason and /today link; the same tick cycle proves the
resolved message once the disruption clears; and, at the HTTP layer,
unlinking a channel only removes that one and "send test message" sends
exactly one message through the fake.

Offline throughout: the scheduler test replays the real recorded
tests/fixtures/hafas/recorded/departures_cancellation.json via
httpx.MockTransport (the second tick's "undisturbed" departures are an
empty list, needing no fixture of its own), and `/journeys` is stubbed to
no alternatives so the message text stays predictable. The HTTP tests use
FastAPI's TestClient and never contact Telegram or ntfy.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pendel import db
from pendel.app import app, get_channels
from pendel.commute import BERLIN, Commute
from pendel.hafas import HafasClient
from pendel.notify import FakeChannel
from pendel.scheduler import Scheduler

RECORDED = Path(__file__).parent / "fixtures" / "hafas" / "recorded"

# Real 9-digit stop ids (charter: real stop ids are 9 digits), both drawn
# from stops already used elsewhere in the test suite.
_WESTKREUZ_STOP_ID = "900024102"  # origin of the recorded cancellation fixture
_HAUPTBAHNHOF_STOP_ID = "900003201"  # any other real stop id; departures() is never called for it

# Wednesday 2026-09-30, matching the recorded fixture's S46 07:20 departure.
_NOW = dt.datetime(2026, 9, 30, 7, 20, tzinfo=BERLIN)


def _cancellation_departures() -> dict:
    return json.loads((RECORDED / "departures_cancellation.json").read_text())


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDEL_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def conn(data_dir):
    connection = db.connect()
    yield connection
    connection.close()


def _commute() -> Commute:
    return Commute(
        origin_stop_id=_WESTKREUZ_STOP_ID,
        destination_stop_id=_HAUPTBAHNHOF_STOP_ID,
        lines=frozenset({"S46"}),
        weekdays=frozenset({2}),  # Wednesday
        window_start=dt.time(7, 15),
        window_end=dt.time(7, 25),
        origin_name="Westkreuz",
        destination_name="Hohenzollerndamm",
    )


def test_scheduler_tick_sends_a_disruption_message_then_a_resolved_message(conn):
    user_id = db.create_user(conn)
    commute_id = db.add_commute(conn, user_id, _commute())
    conn.execute(
        "INSERT INTO channels (user_id, kind, target, linked_at) "
        "VALUES (?, 'ntfy', 'topic-abc', datetime('now'))",
        (user_id,),
    )
    conn.commit()
    fake = FakeChannel()

    responses = iter([_cancellation_departures(), {"departures": []}])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/journeys":
            return httpx.Response(200, json={"journeys": []})
        return httpx.Response(200, json=next(responses))

    hafas = HafasClient(httpx.Client(transport=httpx.MockTransport(handler)))
    sched = Scheduler(conn, hafas, {"ntfy": fake}, clock=lambda: None)

    sched.tick(_NOW)

    assert len(fake.sent) == 1
    target, text = fake.sent[0]
    assert target == "topic-abc"
    assert "Westkreuz → Hohenzollerndamm" in text
    assert "S46" in text
    assert "07:20" in text
    assert "fällt aus" in text  # DE reason (cancellation)
    assert "is cancelled" in text  # EN reason
    assert "/today" in text

    sched.tick(_NOW + dt.timedelta(minutes=1))

    assert len(fake.sent) == 2
    resolved_target, resolved_text = fake.sent[1]
    assert resolved_target == "topic-abc"
    assert "Westkreuz → Hohenzollerndamm" in resolved_text
    assert "Ausfall auf S46 behoben" in resolved_text
    assert "Cancellation on S46 resolved" in resolved_text
    assert "/today" in resolved_text

    rows = conn.execute(
        "SELECT state FROM notifications WHERE commute_id = ?", (commute_id,)
    ).fetchall()
    assert [row["state"] for row in rows] == ["resolved"]


@pytest.fixture
def client():
    test_client = TestClient(app)
    yield test_client
    test_client.cookies.clear()


def _seed_user(tmp_path) -> str:
    connection = db.connect(tmp_path / "pendel.db")
    try:
        return db.create_user(connection)
    finally:
        connection.close()


def _insert_channel(tmp_path, user_id: str, kind: str, target: str) -> int:
    connection = db.connect(tmp_path / "pendel.db")
    try:
        cur = connection.execute(
            "INSERT INTO channels (user_id, kind, target, linked_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (user_id, kind, target),
        )
        connection.commit()
        return cur.lastrowid
    finally:
        connection.close()


def _channel_rows(tmp_path, user_id: str) -> list[sqlite3.Row]:
    connection = db.connect(tmp_path / "pendel.db")
    try:
        return connection.execute(
            "SELECT id, kind FROM channels WHERE user_id = ?", (user_id,)
        ).fetchall()
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def _data_dir_for_http(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDEL_DATA_DIR", str(tmp_path))
    return tmp_path


def test_http_unlink_removes_only_that_channel(client, tmp_path) -> None:
    uid = _seed_user(tmp_path)
    telegram_id = _insert_channel(tmp_path, uid, "telegram", "chat-1")
    ntfy_id = _insert_channel(tmp_path, uid, "ntfy", "topic-1")
    client.cookies.set("uid", uid)

    response = client.post(
        f"/notifications/channels/{telegram_id}/unlink", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/notifications"
    rows = _channel_rows(tmp_path, uid)
    assert [row["id"] for row in rows] == [ntfy_id]


@pytest.fixture
def fake_channels():
    fake = FakeChannel()
    app.dependency_overrides[get_channels] = lambda: {"telegram": fake, "ntfy": fake}
    yield fake
    app.dependency_overrides.pop(get_channels, None)


def test_http_send_test_message_sends_exactly_one_message(
    client, tmp_path, fake_channels
) -> None:
    uid = _seed_user(tmp_path)
    channel_id = _insert_channel(tmp_path, uid, "ntfy", "topic-1")
    client.cookies.set("uid", uid)

    response = client.post(
        f"/notifications/channels/{channel_id}/test", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/notifications?test=sent"
    assert len(fake_channels.sent) == 1
    assert fake_channels.sent[0][0] == "topic-1"

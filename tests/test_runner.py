"""Tests for the `python -m pendel.runner` entrypoint (charter G3, T-0031):
`build_channels` builds real channels only for configured kinds and a
no-op otherwise, and `main(['--once'])` wires a real `db` connection, a
real `HafasClient` and the built channels into exactly one `Scheduler.tick`.

Nothing here touches the network: `conftest._block_network` blocks real
sockets, and `main` is exercised with `_build_http_client` monkeypatched to
an `httpx.MockTransport` and `_clock` monkeypatched to a fixed instant.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from pendel import db, runner
from pendel.commute import BERLIN
from pendel.notify import NtfyChannel, TelegramChannel

FIXTURES = Path(__file__).parent / "fixtures" / "hafas" / "scheduler"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _berlin(hour: int, minute: int) -> dt.datetime:
    return dt.datetime(2026, 1, 5, hour, minute, tzinfo=BERLIN)  # Monday, winter (+01:00)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDEL_DATA_DIR", str(tmp_path))
    return tmp_path


class _RecordingClient:
    """Dummy httpx.Client stand-in: build_channels only needs *a* client to
    pass to the adapter constructors, it never sends anything itself."""


def test_build_channels_neither_configured_are_noops_that_send_nothing():
    channels = runner.build_channels(_RecordingClient(), {})

    assert set(channels) == {"telegram", "ntfy"}
    channels["telegram"].send("chat-1", "hello")
    channels["ntfy"].send("topic-1", "hello")


def test_build_channels_telegram_only():
    channels = runner.build_channels(
        _RecordingClient(), {"PENDEL_TELEGRAM_BOT_TOKEN": "tok"}
    )

    assert isinstance(channels["telegram"], TelegramChannel)
    channels["ntfy"].send("topic-1", "hello")  # no-op, must not raise


def test_build_channels_ntfy_only():
    channels = runner.build_channels(
        _RecordingClient(), {"PENDEL_NTFY_URL": "http://ntfy.invalid"}
    )

    assert isinstance(channels["ntfy"], NtfyChannel)
    channels["telegram"].send("chat-1", "hello")  # no-op, must not raise


def test_build_channels_both_configured():
    channels = runner.build_channels(
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        {"PENDEL_TELEGRAM_BOT_TOKEN": "tok", "PENDEL_NTFY_URL": "http://ntfy.invalid"},
    )

    assert isinstance(channels["telegram"], TelegramChannel)
    assert isinstance(channels["ntfy"], NtfyChannel)


def _insert_commute(conn: sqlite3.Connection, user_id: str) -> int:
    cur = conn.execute(
        "INSERT INTO commutes (user_id, origin_stop_id, destination_stop_id, "
        "lines, weekdays, window_start, window_end, delay_threshold_min) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, "900000100001", "900000200002", "S41", "0,1,2,3,4", "06:00", "08:00", 5),
    )
    conn.commit()
    return cur.lastrowid


def _insert_ntfy_channel(conn: sqlite3.Connection, user_id: str, topic: str) -> None:
    conn.execute(
        "INSERT INTO channels (user_id, kind, target) VALUES (?, 'ntfy', ?)",
        (user_id, topic),
    )
    conn.commit()


def test_main_once_sends_exactly_one_ntfy_request_for_a_due_affected_commute(
    data_dir, monkeypatch
):
    monkeypatch.setenv("PENDEL_NTFY_URL", "http://ntfy.invalid")
    monkeypatch.setattr(runner, "_clock", lambda: _berlin(6, 30))

    conn = db.connect()
    user_id = db.create_user(conn)
    _insert_commute(conn, user_id)
    _insert_ntfy_channel(conn, user_id, "topic-abc")
    conn.close()

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/journeys":
            return httpx.Response(200, json={"journeys": []})
        if request.url.host == "ntfy.invalid":
            return httpx.Response(200)
        return httpx.Response(200, json=_load("synthetic_ersatzverkehr.json"))

    monkeypatch.setattr(
        runner,
        "_build_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    runner.main(["--once"])

    ntfy_requests = [r for r in requests if r.url.host == "ntfy.invalid"]
    assert len(ntfy_requests) == 1
    assert ntfy_requests[0].url.path == "/topic-abc"


def test_main_once_with_no_due_commutes_sends_nothing(data_dir, monkeypatch):
    monkeypatch.setenv("PENDEL_NTFY_URL", "http://ntfy.invalid")
    monkeypatch.setattr(runner, "_clock", lambda: _berlin(6, 30))

    conn = db.connect()
    conn.close()

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"departures": []})

    monkeypatch.setattr(
        runner,
        "_build_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )

    runner.main(["--once"])

    assert requests == []

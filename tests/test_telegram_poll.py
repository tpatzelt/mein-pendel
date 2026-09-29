"""Tests for `pendel.telegram_poll` (charter G3, T-0036): `poll_once` feeds
`getUpdates` results into `telegram_link.handle_update`, `run_forever` loops
it with a backoff on failure, and `main()` wires the two together from
`PENDEL_TELEGRAM_BOT_TOKEN`.

Everything here goes through `httpx.MockTransport`; `conftest._block_network`
also blocks any real socket, and no request in this file ever names
api.telegram.org as anything but a fixed fake host. `bot_token` is always the
dummy string 'dummy-token', never a real credential.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from pendel import db, telegram_poll
from pendel.notify import FakeChannel
from pendel.telegram_link import CONFIRMATION_TEXT, create_link
from pendel.telegram_poll import TelegramPollError, poll_once, run_forever

BOT_TOKEN = "dummy-token"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDEL_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def conn(data_dir):
    connection = db.connect()
    yield connection
    connection.close()


@pytest.fixture
def bot_username(monkeypatch):
    monkeypatch.setenv("PENDEL_TELEGRAM_BOT_USERNAME", "pendel_test_bot")


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok(result: list) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result})


def _update(update_id: int, text: str, chat_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {"message_id": update_id, "chat": {"id": chat_id}, "text": text},
    }


def _token_from_url(url: str) -> str:
    return url.rsplit("start=", 1)[1]


# --- poll_once: real end-to-end link -----------------------------------------


def test_poll_once_valid_start_update_links_and_sends_one_confirmation(conn, bot_username):
    user_id = db.create_user(conn)
    token = _token_from_url(create_link(conn, user_id))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _ok([_update(1, f"/start {token}", chat_id=555)])

    channel = FakeChannel()
    new_offset = poll_once(conn, _client(handler), BOT_TOKEN, channel, 0)

    assert new_offset == 2
    row = conn.execute(
        "SELECT * FROM channels WHERE user_id = ? AND kind = 'telegram'", (user_id,)
    ).fetchone()
    assert row["target"] == "555"
    assert channel.sent == [("555", CONFIRMATION_TEXT)]
    assert len(requests) == 1
    assert requests[0].url.path == f"/bot{BOT_TOKEN}/getUpdates"
    assert requests[0].url.host == "api.telegram.org"


# --- poll_once: offset bookkeeping -------------------------------------------


def test_poll_once_offset_advances_and_next_request_carries_it(conn):
    requests: list[httpx.Request] = []
    responses = [_ok([_update(5, "hello"), _update(7, "world")]), _ok([])]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responses.pop(0)

    client = _client(handler)
    channel = FakeChannel()

    offset = poll_once(conn, client, BOT_TOKEN, channel, 0)
    assert offset == 8

    poll_once(conn, client, BOT_TOKEN, channel, offset)

    assert requests[0].url.params["offset"] == "0"
    assert requests[1].url.params["offset"] == "8"


def test_poll_once_empty_result_returns_offset_unchanged(conn):
    channel = FakeChannel()
    client = _client(lambda r: _ok([]))

    offset = poll_once(conn, client, BOT_TOKEN, channel, 42)

    assert offset == 42


# --- poll_once: errors never leak the URL or the token -----------------------


def test_poll_once_http_error_raises_without_leaking_token_or_url(conn):
    channel = FakeChannel()
    client = _client(lambda r: httpx.Response(500))

    with pytest.raises(TelegramPollError) as exc_info:
        poll_once(conn, client, BOT_TOKEN, channel, 0)

    message = str(exc_info.value)
    assert BOT_TOKEN not in message
    assert "api.telegram.org" not in message


def test_poll_once_ok_false_raises_without_leaking_token_or_url(conn):
    channel = FakeChannel()
    client = _client(
        lambda r: httpx.Response(200, json={"ok": False, "description": "boom"})
    )

    with pytest.raises(TelegramPollError) as exc_info:
        poll_once(conn, client, BOT_TOKEN, channel, 0)

    message = str(exc_info.value)
    assert BOT_TOKEN not in message
    assert "api.telegram.org" not in message


# --- poll_once: per-update isolation -----------------------------------------


def test_poll_once_skips_update_that_raises_and_still_processes_the_next(conn, monkeypatch):
    processed: list[int] = []

    def fake_handle_update(conn, update, channel):
        if update["update_id"] == 1:
            raise RuntimeError("boom")
        processed.append(update["update_id"])

    monkeypatch.setattr(telegram_poll, "handle_update", fake_handle_update)
    channel = FakeChannel()
    client = _client(
        lambda r: _ok(["not-a-dict", _update(1, "/start x"), _update(2, "/start y")])
    )

    offset = poll_once(conn, client, BOT_TOKEN, channel, 0)

    assert processed == [2]
    assert offset == 3


# --- run_forever ---------------------------------------------------------------


def test_run_forever_survives_a_failing_poll_and_backs_off_with_sleep(conn):
    class Stop(Exception):
        pass

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _client(handler)
    channel = FakeChannel()
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise Stop

    with pytest.raises(Stop):
        run_forever(conn, client, BOT_TOKEN, channel, sleep=fake_sleep)

    assert sleeps == [5, 5, 5]


# --- main() ----------------------------------------------------------------


def test_main_without_token_returns_without_connecting_or_building_a_client(
    data_dir, monkeypatch
):
    monkeypatch.delenv("PENDEL_TELEGRAM_BOT_TOKEN", raising=False)
    calls: list[str] = []
    monkeypatch.setattr(
        telegram_poll.db, "connect", lambda *a, **k: calls.append("connect")
    )
    monkeypatch.setattr(
        telegram_poll.httpx, "Client", lambda *a, **k: calls.append("client")
    )

    telegram_poll.main()

    assert calls == []


def test_main_with_token_never_logs_the_token_at_info(data_dir, monkeypatch, caplog):
    monkeypatch.setenv("PENDEL_TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    logging.getLogger("httpcore").setLevel(logging.NOTSET)

    class StopAfterConnect(Exception):
        pass

    def fake_connect(*args, **kwargs):
        raise StopAfterConnect

    monkeypatch.setattr(telegram_poll.db, "connect", fake_connect)

    with caplog.at_level(logging.INFO):
        with pytest.raises(StopAfterConnect):
            telegram_poll.main()

    assert all(BOT_TOKEN not in record.getMessage() for record in caplog.records)
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING

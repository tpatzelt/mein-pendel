from __future__ import annotations

import pytest

from pendel import db
from pendel.notify import FakeChannel
from pendel.telegram_link import (
    CONFIRMATION_TEXT,
    INVALID_TOKEN_TEXT,
    TelegramLinkConfigError,
    create_link,
    handle_update,
)


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
    name = "pendel_test_bot"
    monkeypatch.setenv("PENDEL_TELEGRAM_BOT_USERNAME", name)
    return name


def _channel_row(conn, user_id):
    return conn.execute(
        "SELECT * FROM channels WHERE user_id = ? AND kind = 'telegram'", (user_id,)
    ).fetchone()


def _start_update(token: str | None, chat_id: int = 424242) -> dict:
    text = "/start" if token is None else f"/start {token}"
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def _token_from_url(url: str) -> str:
    return url.rsplit("start=", 1)[1]


# --- create_link -------------------------------------------------------------


def test_create_link_requires_bot_username_env(conn, monkeypatch):
    monkeypatch.delenv("PENDEL_TELEGRAM_BOT_USERNAME", raising=False)
    user_id = db.create_user(conn)
    with pytest.raises(TelegramLinkConfigError):
        create_link(conn, user_id)


def test_create_link_returns_deep_link_url(conn, bot_username):
    user_id = db.create_user(conn)
    url = create_link(conn, user_id)
    assert url.startswith(f"https://t.me/{bot_username}?start=")


def test_create_link_stores_unlinked_channel_row(conn, bot_username):
    user_id = db.create_user(conn)
    url = create_link(conn, user_id)

    row = _channel_row(conn, user_id)
    assert row is not None
    assert row["target"] is None
    assert row["linked_at"] is None
    assert row["link_token"] == _token_from_url(url)


def test_create_link_tokens_are_unique(conn, bot_username):
    user_id = db.create_user(conn)
    first = _token_from_url(create_link(conn, user_id))
    second = _token_from_url(create_link(conn, user_id))
    assert first != second


# --- handle_update: end to end -----------------------------------------------


def test_start_with_valid_token_links_and_sends_one_confirmation(conn, bot_username):
    user_id = db.create_user(conn)
    url = create_link(conn, user_id)
    token = _token_from_url(url)
    channel = FakeChannel()

    handle_update(conn, _start_update(token, chat_id=555), channel)

    row = _channel_row(conn, user_id)
    assert row["target"] == "555"
    assert row["linked_at"] is not None
    assert row["link_token"] is None
    assert channel.sent == [("555", CONFIRMATION_TEXT)]


def test_replaying_the_same_update_does_not_relink_or_reconfirm(conn, bot_username):
    user_id = db.create_user(conn)
    token = _token_from_url(create_link(conn, user_id))
    channel = FakeChannel()
    update = _start_update(token, chat_id=555)

    handle_update(conn, update, channel)
    row_after_first = dict(_channel_row(conn, user_id))
    handle_update(conn, update, channel)
    row_after_second = dict(_channel_row(conn, user_id))

    assert row_after_first == row_after_second
    confirmations = [msg for msg in channel.sent if msg[1] == CONFIRMATION_TEXT]
    assert len(confirmations) == 1


def test_wrong_token_links_nothing_and_reveals_nothing(conn, bot_username):
    user_id = db.create_user(conn)
    real_token = _token_from_url(create_link(conn, user_id))
    wrong_token = real_token[::-1] + "x"
    channel = FakeChannel()

    handle_update(conn, _start_update(wrong_token, chat_id=999), channel)

    row = _channel_row(conn, user_id)
    assert row["target"] is None
    assert row["linked_at"] is None
    assert row["link_token"] == real_token
    assert channel.sent == [("999", INVALID_TOKEN_TEXT)]


def test_unknown_token_with_no_rows_at_all_sends_neutral_reply(conn, bot_username):
    channel = FakeChannel()

    handle_update(conn, _start_update("anything", chat_id=999), channel)

    assert channel.sent == [("999", INVALID_TOKEN_TEXT)]
    assert conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 0


def test_malformed_start_with_no_token_sends_neutral_reply_and_links_nothing(
    conn, bot_username
):
    user_id = db.create_user(conn)
    create_link(conn, user_id)
    channel = FakeChannel()

    handle_update(conn, _start_update(None, chat_id=999), channel)

    row = _channel_row(conn, user_id)
    assert row["target"] is None
    assert channel.sent == [("999", INVALID_TOKEN_TEXT)]


def test_reply_for_unknown_token_matches_reply_for_already_used_token(conn, bot_username):
    """The neutral reply must not reveal whether a token ever existed."""
    used_user = db.create_user(conn)
    used_token = _token_from_url(create_link(conn, used_user))
    channel_a = FakeChannel()
    handle_update(conn, _start_update(used_token, chat_id=1), channel_a)  # consumes it

    channel_used = FakeChannel()
    handle_update(conn, _start_update(used_token, chat_id=2), channel_used)

    channel_unknown = FakeChannel()
    handle_update(conn, _start_update(used_token[::-1] + "z", chat_id=3), channel_unknown)

    assert [text for _target, text in channel_used.sent] == [INVALID_TOKEN_TEXT]
    assert [text for _target, text in channel_unknown.sent] == [INVALID_TOKEN_TEXT]


def test_non_start_message_is_ignored(conn, bot_username):
    user_id = db.create_user(conn)
    create_link(conn, user_id)
    channel = FakeChannel()

    handle_update(
        conn,
        {"update_id": 1, "message": {"message_id": 1, "chat": {"id": 1}, "text": "hello"}},
        channel,
    )

    assert channel.sent == []
    row = _channel_row(conn, user_id)
    assert row["target"] is None


def test_update_without_message_is_ignored(conn, bot_username):
    channel = FakeChannel()
    handle_update(conn, {"update_id": 1}, channel)
    assert channel.sent == []


def test_only_the_matching_users_row_is_linked(conn, bot_username):
    user_a = db.create_user(conn)
    user_b = db.create_user(conn)
    token_a = _token_from_url(create_link(conn, user_a))
    create_link(conn, user_b)
    channel = FakeChannel()

    handle_update(conn, _start_update(token_a, chat_id=1), channel)

    row_a = _channel_row(conn, user_a)
    row_b = _channel_row(conn, user_b)
    assert row_a["target"] == "1"
    assert row_b["target"] is None

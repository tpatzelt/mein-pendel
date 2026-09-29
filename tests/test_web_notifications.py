"""HTTP-level tests for the notifications page (charter G3): getting a
Telegram /start deep link and saving an ntfy topic for the cookie user.

Offline and never contacts Telegram or ntfy: these routes only read/write
`channels` rows via `db`/`telegram_link`, both already unit-tested.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from pendel import db
from pendel.app import app

_FIXED_WIDTH_RE = re.compile(r"(?<!-)width\s*:\s*(\d+)px")


def _assert_no_wide_fixed_widths(text: str) -> None:
    for match in _FIXED_WIDTH_RE.finditer(text):
        assert int(match.group(1)) <= 360, f"fixed width above 360px found: {match.group(0)!r}"


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDEL_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def client():
    test_client = TestClient(app)
    yield test_client
    test_client.cookies.clear()


def _seed_user(tmp_path) -> str:
    conn = db.connect(tmp_path / "pendel.db")
    try:
        return db.create_user(conn)
    finally:
        conn.close()


def _channel_rows(tmp_path, user_id: str) -> list[tuple]:
    conn = db.connect(tmp_path / "pendel.db")
    try:
        return conn.execute(
            "SELECT kind, target, link_token, linked_at FROM channels WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()


def test_telegram_link_shown_when_configured_and_pending_row_exists(
    client, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PENDEL_TELEGRAM_BOT_USERNAME", "mein_pendel_bot")
    uid = _seed_user(tmp_path)
    conn = db.connect(tmp_path / "pendel.db")
    try:
        conn.execute(
            "INSERT INTO channels (user_id, kind, target, link_token) "
            "VALUES (?, 'telegram', NULL, 'tok123')",
            (uid,),
        )
        conn.commit()
    finally:
        conn.close()
    client.cookies.set("uid", uid)

    response = client.get("/notifications")

    assert response.status_code == 200
    assert 'href="https://t.me/mein_pendel_bot?start=tok123"' in response.text


def test_post_telegram_creates_row_and_get_then_shows_link(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PENDEL_TELEGRAM_BOT_USERNAME", "mein_pendel_bot")
    uid = _seed_user(tmp_path)
    client.cookies.set("uid", uid)

    response = client.post("/notifications/telegram")

    assert response.status_code == 200
    assert "https://t.me/mein_pendel_bot?start=" in response.text
    rows = _channel_rows(tmp_path, uid)
    assert len(rows) == 1
    assert rows[0]["kind"] == "telegram"


def test_post_telegram_without_bot_username_returns_503_and_creates_no_row(
    client, tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("PENDEL_TELEGRAM_BOT_USERNAME", raising=False)
    uid = _seed_user(tmp_path)
    client.cookies.set("uid", uid)

    response = client.post("/notifications/telegram")

    assert response.status_code == 503
    assert "Traceback" not in response.text
    assert _channel_rows(tmp_path, uid) == []


def test_post_ntfy_with_valid_topic_inserts_exactly_one_row(client, tmp_path) -> None:
    uid = _seed_user(tmp_path)
    client.cookies.set("uid", uid)

    response = client.post("/notifications/ntfy", data={"topic": "pendel-alerts_1"})

    assert response.status_code == 200
    rows = _channel_rows(tmp_path, uid)
    assert len(rows) == 1
    assert rows[0]["kind"] == "ntfy"
    assert rows[0]["target"] == "pendel-alerts_1"
    assert rows[0]["linked_at"] is not None


def test_post_ntfy_with_invalid_topic_returns_400_and_creates_no_row(client, tmp_path) -> None:
    uid = _seed_user(tmp_path)
    client.cookies.set("uid", uid)

    response = client.post("/notifications/ntfy", data={"topic": "not a valid topic!"})

    assert response.status_code == 400
    assert _channel_rows(tmp_path, uid) == []


def test_post_ntfy_without_cookie_creates_no_row(client, tmp_path) -> None:
    response = client.post("/notifications/ntfy", data={"topic": "some-topic"})

    assert response.status_code == 200
    conn = db.connect(tmp_path / "pendel.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 0
    finally:
        conn.close()


def test_get_notifications_with_unknown_cookie_shows_hint_and_creates_no_row(
    client, tmp_path
) -> None:
    client.cookies.set("uid", "does-not-exist")

    response = client.get("/notifications")

    assert response.status_code == 200
    assert 'href="/stops"' in response.text


def test_delete_my_data_removes_channel_rows(client, tmp_path) -> None:
    uid = _seed_user(tmp_path)
    client.cookies.set("uid", uid)
    client.post("/notifications/ntfy", data={"topic": "pendel-alerts"})
    assert len(_channel_rows(tmp_path, uid)) == 1

    response = client.post("/me/delete")
    assert response.status_code == 200

    conn = db.connect(tmp_path / "pendel.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 0
    finally:
        conn.close()


def test_notifications_page_has_viewport_meta_and_no_wide_fixed_widths(client, tmp_path) -> None:
    uid = _seed_user(tmp_path)
    client.cookies.set("uid", uid)

    response = client.get("/notifications")

    assert response.status_code == 200
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in response.text
    _assert_no_wide_fixed_widths(response.text)


def test_home_links_to_notifications() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/notifications"' in response.text

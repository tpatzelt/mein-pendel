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
from pendel.app import app, get_channels
from pendel.notify import ChannelSendError, FakeChannel

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


def _insert_telegram_channel(tmp_path, user_id: str, target: str = "12345") -> int:
    conn = db.connect(tmp_path / "pendel.db")
    try:
        cur = conn.execute(
            "INSERT INTO channels (user_id, kind, target, linked_at) "
            "VALUES (?, 'telegram', ?, datetime('now'))",
            (user_id, target),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _insert_ntfy_channel(tmp_path, user_id: str, topic: str = "alerts-1") -> int:
    conn = db.connect(tmp_path / "pendel.db")
    try:
        return db.add_ntfy_channel(conn, user_id, topic)
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


def test_notifications_page_shows_empty_line_when_no_channels(client, tmp_path) -> None:
    uid = _seed_user(tmp_path)
    client.cookies.set("uid", uid)

    response_de = client.get("/notifications")
    assert response_de.status_code == 200
    assert "Noch keine Kanäle verknüpft." in response_de.text

    response_en = client.get("/notifications?lang=en")
    assert response_en.status_code == 200
    assert "No channels linked yet." in response_en.text


def test_notifications_page_lists_channels_with_kind_name_and_status(client, tmp_path) -> None:
    uid = _seed_user(tmp_path)
    telegram_id = _insert_telegram_channel(tmp_path, uid, target="12345")
    ntfy_id = _insert_ntfy_channel(tmp_path, uid, topic="alerts-1")
    client.cookies.set("uid", uid)

    response_de = client.get("/notifications")
    assert response_de.status_code == 200
    assert "Telegram: Verknüpft: •2345" in response_de.text
    assert "ntfy: Verknüpft: " in response_de.text
    assert len(re.findall(r'action="/notifications/channels/\d+/unlink"', response_de.text)) == 2
    assert 'aria-label="Telegram trennen"' in response_de.text
    assert 'aria-label="ntfy trennen"' in response_de.text
    assert f'action="/notifications/channels/{telegram_id}/unlink"' in response_de.text
    assert f'action="/notifications/channels/{ntfy_id}/unlink"' in response_de.text

    response_en = client.get("/notifications?lang=en")
    assert response_en.status_code == 200
    assert "Telegram: Linked: •2345" in response_en.text
    assert 'aria-label="Unlink Telegram"' in response_en.text
    assert 'aria-label="Unlink ntfy"' in response_en.text


def test_notifications_page_shows_pending_status_for_unlinked_channel(client, tmp_path) -> None:
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

    response_de = client.get("/notifications")
    assert response_de.status_code == 200
    assert "Telegram: Ausstehend" in response_de.text

    response_en = client.get("/notifications?lang=en")
    assert response_en.status_code == 200
    assert "Telegram: Pending" in response_en.text


def test_unlink_removes_only_that_channel(client, tmp_path) -> None:
    uid = _seed_user(tmp_path)
    telegram_id = _insert_telegram_channel(tmp_path, uid)
    ntfy_id = _insert_ntfy_channel(tmp_path, uid)
    client.cookies.set("uid", uid)

    response = client.post(
        f"/notifications/channels/{telegram_id}/unlink", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/notifications"

    rows = _channel_rows(tmp_path, uid)
    assert len(rows) == 1
    assert rows[0]["kind"] == "ntfy"

    follow_up = client.get("/notifications")
    assert 'data-channel-kind="ntfy"' in follow_up.text
    assert 'data-channel-kind="telegram"' not in follow_up.text
    assert f"/notifications/channels/{telegram_id}/unlink" not in follow_up.text
    assert f"/notifications/channels/{ntfy_id}/unlink" in follow_up.text


def test_unlink_foreign_channel_is_a_no_op(client, tmp_path) -> None:
    uid = _seed_user(tmp_path)
    channel_id = _insert_ntfy_channel(tmp_path, uid)
    stranger = _seed_user(tmp_path)
    client.cookies.set("uid", stranger)

    response = client.post(
        f"/notifications/channels/{channel_id}/unlink", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/notifications"
    assert len(_channel_rows(tmp_path, uid)) == 1


def test_unlink_unknown_channel_is_a_no_op(client, tmp_path) -> None:
    uid = _seed_user(tmp_path)
    client.cookies.set("uid", uid)

    response = client.post("/notifications/channels/999999/unlink", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/notifications"


def test_unlink_without_cookie_is_a_no_op(client, tmp_path) -> None:
    uid = _seed_user(tmp_path)
    channel_id = _insert_ntfy_channel(tmp_path, uid)

    response = client.post(
        f"/notifications/channels/{channel_id}/unlink", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/notifications"
    assert len(_channel_rows(tmp_path, uid)) == 1


class _RaisingChannel:
    """A Channel whose send() always fails, to exercise the ChannelSendError path."""

    def __init__(self) -> None:
        self.calls = 0

    def send(self, target: str, text: str) -> None:
        self.calls += 1
        raise ChannelSendError("boom")


@pytest.fixture
def fake_channels():
    fake = FakeChannel()
    app.dependency_overrides[get_channels] = lambda: {"telegram": fake, "ntfy": fake}
    yield fake
    app.dependency_overrides.pop(get_channels, None)


def _insert_pending_channel(tmp_path, user_id: str, kind: str = "telegram") -> int:
    conn = db.connect(tmp_path / "pendel.db")
    try:
        cur = conn.execute(
            "INSERT INTO channels (user_id, kind, target, link_token) "
            "VALUES (?, ?, NULL, 'tok123')",
            (user_id, kind),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_test_message_sends_exactly_one_message_to_linked_channel(
    client, tmp_path, fake_channels
) -> None:
    uid = _seed_user(tmp_path)
    channel_id = _insert_telegram_channel(tmp_path, uid, target="12345")
    client.cookies.set("uid", uid)

    response = client.post(
        f"/notifications/channels/{channel_id}/test", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/notifications?test=sent"
    assert fake_channels.sent == [("12345", "Testnachricht von Mein Pendel")]

    follow_up = client.get("/notifications?test=sent")
    assert follow_up.status_code == 200
    assert 'role="status"' in follow_up.text


def test_test_message_pending_channel_sends_nothing(client, tmp_path, fake_channels) -> None:
    uid = _seed_user(tmp_path)
    channel_id = _insert_pending_channel(tmp_path, uid)
    client.cookies.set("uid", uid)

    response = client.post(
        f"/notifications/channels/{channel_id}/test", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/notifications"
    assert fake_channels.sent == []


def test_test_message_foreign_channel_sends_nothing(client, tmp_path, fake_channels) -> None:
    uid = _seed_user(tmp_path)
    channel_id = _insert_ntfy_channel(tmp_path, uid)
    stranger = _seed_user(tmp_path)
    client.cookies.set("uid", stranger)

    response = client.post(
        f"/notifications/channels/{channel_id}/test", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/notifications"
    assert fake_channels.sent == []


def test_test_message_unknown_channel_sends_nothing(client, tmp_path, fake_channels) -> None:
    uid = _seed_user(tmp_path)
    client.cookies.set("uid", uid)

    response = client.post("/notifications/channels/999999/test", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/notifications"
    assert fake_channels.sent == []


def test_test_message_without_cookie_sends_nothing(client, tmp_path, fake_channels) -> None:
    uid = _seed_user(tmp_path)
    channel_id = _insert_ntfy_channel(tmp_path, uid)

    response = client.post(
        f"/notifications/channels/{channel_id}/test", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/notifications"
    assert fake_channels.sent == []


def test_test_message_send_error_shows_alert_and_502(client, tmp_path) -> None:
    uid = _seed_user(tmp_path)
    channel_id = _insert_ntfy_channel(tmp_path, uid)
    raising = _RaisingChannel()
    app.dependency_overrides[get_channels] = lambda: {"telegram": raising, "ntfy": raising}
    client.cookies.set("uid", uid)

    try:
        response = client.post(
            f"/notifications/channels/{channel_id}/test", follow_redirects=False
        )
    finally:
        app.dependency_overrides.pop(get_channels, None)

    assert response.status_code == 502
    assert 'role="alert"' in response.text
    assert "Testnachricht konnte nicht gesendet werden." in response.text
    assert raising.calls == 1


def test_notifications_page_shows_test_button_only_for_linked_channels(
    client, tmp_path
) -> None:
    uid = _seed_user(tmp_path)
    linked_id = _insert_telegram_channel(tmp_path, uid)
    _insert_pending_channel(tmp_path, uid, kind="ntfy")
    client.cookies.set("uid", uid)

    response = client.get("/notifications")

    assert response.status_code == 200
    test_actions = re.findall(r'action="/notifications/channels/(\d+)/test"', response.text)
    assert test_actions == [str(linked_id)]

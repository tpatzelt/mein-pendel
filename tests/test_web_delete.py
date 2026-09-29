"""HTTP-level tests for POST /me/delete (charter G5): delete my data in one
click. The route removes every row for the `uid` cookie's user via
db.delete_user's ON DELETE CASCADE and clears the cookie.

Tests are offline; the DB dependency is exercised against a real, migrated
SQLite file under `tmp_path` via PENDEL_DATA_DIR.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pendel import db
from pendel.app import app
from pendel.i18n import STRINGS


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDEL_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def client():
    test_client = TestClient(app)
    yield test_client
    test_client.cookies.clear()


def _seed_user_with_data(conn: db.sqlite3.Connection) -> str:
    user_id = db.create_user(conn)
    conn.execute(
        "INSERT INTO commutes "
        "(user_id, origin_stop_id, destination_stop_id, lines, weekdays, window_start, window_end) "
        "VALUES (?, 'A', 'B', 'S1', '0', '07:00', '08:00')",
        (user_id,),
    )
    conn.execute(
        "INSERT INTO channels (user_id, kind, target) VALUES (?, 'ntfy', 'topic')",
        (user_id,),
    )
    (commute_id,) = conn.execute(
        "SELECT id FROM commutes WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.execute(
        "INSERT INTO notifications (commute_id, disruption_key, state, first_notified_at) "
        "VALUES (?, 'k', 'active', datetime('now'))",
        (commute_id,),
    )
    conn.commit()
    return user_id


def _counts(conn: db.sqlite3.Connection, user_id: str) -> dict[str, int]:
    return {
        "users": conn.execute(
            "SELECT COUNT(*) FROM users WHERE id = ?", (user_id,)
        ).fetchone()[0],
        "commutes": conn.execute(
            "SELECT COUNT(*) FROM commutes WHERE user_id = ?", (user_id,)
        ).fetchone()[0],
        "channels": conn.execute(
            "SELECT COUNT(*) FROM channels WHERE user_id = ?", (user_id,)
        ).fetchone()[0],
        "notifications": conn.execute(
            "SELECT COUNT(*) FROM notifications AS n "
            "JOIN commutes AS c ON c.id = n.commute_id WHERE c.user_id = ?",
            (user_id,),
        ).fetchone()[0],
    }


def test_delete_removes_every_row_for_that_user_and_spares_other_users(client, tmp_path) -> None:
    conn = db.connect(tmp_path / "pendel.db")
    try:
        first_user = _seed_user_with_data(conn)
        second_user = _seed_user_with_data(conn)
    finally:
        conn.close()

    client.cookies.set("uid", first_user)
    response = client.post("/me/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?deleted=1"

    conn = db.connect(tmp_path / "pendel.db")
    try:
        first_counts = _counts(conn, first_user)
        second_counts = _counts(conn, second_user)
    finally:
        conn.close()

    assert first_counts == {"users": 0, "commutes": 0, "channels": 0, "notifications": 0}
    assert second_counts == {"users": 1, "commutes": 1, "channels": 1, "notifications": 1}


def test_delete_clears_the_uid_cookie(client, tmp_path) -> None:
    conn = db.connect(tmp_path / "pendel.db")
    try:
        user_id = _seed_user_with_data(conn)
    finally:
        conn.close()

    client.cookies.set("uid", user_id)
    response = client.post("/me/delete", follow_redirects=False)

    assert response.status_code == 303
    set_cookie = response.headers.get("set-cookie", "")
    assert 'uid=""' in set_cookie
    assert "Max-Age=0" in set_cookie


def test_delete_with_no_cookie_redirects_and_deletes_nothing(client, tmp_path) -> None:
    conn = db.connect(tmp_path / "pendel.db")
    try:
        user_id = _seed_user_with_data(conn)
    finally:
        conn.close()

    response = client.post("/me/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?deleted=1"

    conn = db.connect(tmp_path / "pendel.db")
    try:
        counts = _counts(conn, user_id)
    finally:
        conn.close()
    assert counts == {"users": 1, "commutes": 1, "channels": 1, "notifications": 1}


def test_delete_with_unknown_cookie_redirects_and_deletes_nothing(client, tmp_path) -> None:
    conn = db.connect(tmp_path / "pendel.db")
    try:
        user_id = _seed_user_with_data(conn)
    finally:
        conn.close()

    client.cookies.set("uid", "does-not-exist")
    response = client.post("/me/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?deleted=1"

    conn = db.connect(tmp_path / "pendel.db")
    try:
        counts = _counts(conn, user_id)
    finally:
        conn.close()
    assert counts == {"users": 1, "commutes": 1, "channels": 1, "notifications": 1}


def test_home_shows_confirmation_in_german_and_english_after_delete(client) -> None:
    response_de = client.get("/", params={"deleted": "1"})
    assert response_de.status_code == 200
    assert STRINGS["de"]["delete_me_done"] in response_de.text

    response_en = client.get("/", params={"deleted": "1", "lang": "en"})
    assert response_en.status_code == 200
    assert STRINGS["en"]["delete_me_done"] in response_en.text


def test_home_without_deleted_param_shows_no_confirmation(client) -> None:
    response_de = client.get("/")
    assert response_de.status_code == 200
    assert STRINGS["de"]["delete_me_done"] not in response_de.text

    response_en = client.get("/", params={"lang": "en"})
    assert response_en.status_code == 200
    assert STRINGS["en"]["delete_me_done"] not in response_en.text


def test_home_has_a_no_js_post_form_for_the_delete_button(client) -> None:
    response = client.get("/")
    assert 'action="/me/delete"' in response.text
    assert 'method="post"' in response.text

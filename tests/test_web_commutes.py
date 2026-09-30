"""HTTP-level tests for GET/POST /commutes/new (charter G2): saving a commute
without a password account.

Tests are offline and never call HAFAS; the DB dependency is exercised
against a real, migrated SQLite file under `tmp_path` via PENDEL_DATA_DIR.
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


def _valid_form() -> dict[str, str]:
    return {
        "origin_stop_id": "900000100001",
        "destination_stop_id": "900000200002",
        "lines": "S41, S42",
        "window_start": "07:30",
        "window_end": "08:00",
        "delay_threshold_min": "5",
    }


def _rows_for_only_user(data_dir) -> list:
    conn = db.connect(data_dir / "pendel.db")
    try:
        (user_id,) = (row[0] for row in conn.execute("SELECT id FROM users"))
        return db.list_commutes(conn, user_id)
    finally:
        conn.close()


def test_commutes_new_form_renders_in_german_and_english(client) -> None:
    response_de = client.get("/commutes/new")
    assert response_de.status_code == 200
    assert "<form" in response_de.text

    response_en = client.get("/commutes/new", params={"lang": "en"})
    assert response_en.status_code == 200
    assert "<form" in response_en.text
    assert response_de.text != response_en.text


def test_commutes_new_form_prefills_stop_ids_from_query_params(client) -> None:
    response = client.get(
        "/commutes/new",
        params={"origin_stop_id": "900000100001", "destination_stop_id": "900000200002"},
    )
    assert response.status_code == 200
    assert "900000100001" in response.text
    assert "900000200002" in response.text


def test_commutes_new_form_prefills_both_ids_from_two_step_stop_search_flow(client) -> None:
    response = client.get(
        "/commutes/new",
        params={"origin_stop_id": "900000100001", "destination_stop_id": "900000200002"},
    )
    assert response.status_code == 200
    assert 'value="900000100001"' in response.text
    assert 'value="900000200002"' in response.text


def test_commutes_new_form_shows_search_destination_link_when_only_origin_is_set(client) -> None:
    response_de = client.get("/commutes/new", params={"origin_stop_id": "900000100001"})
    assert response_de.status_code == 200
    assert 'href="/stops?origin_stop_id=900000100001"' in response_de.text
    assert "Ziel suchen" in response_de.text

    response_en = client.get(
        "/commutes/new", params={"origin_stop_id": "900000100001", "lang": "en"}
    )
    assert response_en.status_code == 200
    assert "Search destination" in response_en.text


def test_commutes_new_form_hides_search_destination_link_when_destination_is_set(client) -> None:
    response = client.get(
        "/commutes/new",
        params={"origin_stop_id": "900000100001", "destination_stop_id": "900000200002"},
    )
    assert response.status_code == 200
    assert "/stops?origin_stop_id=" not in response.text


def test_commutes_new_form_hides_search_destination_link_without_origin(client) -> None:
    response = client.get("/commutes/new")
    assert response.status_code == 200
    assert "/stops?origin_stop_id=" not in response.text


def test_commutes_new_form_has_viewport_meta_and_no_wide_fixed_widths(client) -> None:
    response = client.get("/commutes/new")
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in response.text
    _assert_no_wide_fixed_widths(response.text)


def test_valid_post_redirects_sets_cookie_and_inserts_one_row(client, tmp_path) -> None:
    response = client.post("/commutes", data=_valid_form() | {"weekdays": "0"}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    uid = response.cookies.get("uid")
    assert uid

    rows = _rows_for_only_user(tmp_path)
    assert len(rows) == 1
    _, commute = rows[0]
    assert commute.origin_stop_id == "900000100001"
    assert commute.destination_stop_id == "900000200002"
    assert commute.lines == frozenset({"S41", "S42"})
    assert commute.weekdays == frozenset({0})


def test_second_post_with_same_cookie_adds_second_commute_to_same_user(client, tmp_path) -> None:
    first = client.post("/commutes", data=_valid_form() | {"weekdays": "0"}, follow_redirects=False)
    assert first.status_code == 303

    second = client.post(
        "/commutes",
        data=_valid_form() | {"weekdays": ["1", "2"]},
        follow_redirects=False,
    )
    assert second.status_code == 303

    conn = db.connect(tmp_path / "pendel.db")
    try:
        user_ids = {row[0] for row in conn.execute("SELECT id FROM users")}
        assert len(user_ids) == 1
        (user_id,) = user_ids
        rows = db.list_commutes(conn, user_id)
    finally:
        conn.close()
    assert len(rows) == 2


def test_post_with_unknown_uid_cookie_is_treated_as_no_cookie(client, tmp_path) -> None:
    client.cookies.set("uid", "does-not-exist")

    response = client.post("/commutes", data=_valid_form() | {"weekdays": "0"}, follow_redirects=False)

    assert response.status_code == 303
    new_uid = response.cookies.get("uid")
    assert new_uid != "does-not-exist"

    conn = db.connect(tmp_path / "pendel.db")
    try:
        user_ids = {row[0] for row in conn.execute("SELECT id FROM users")}
    finally:
        conn.close()
    assert user_ids == {new_uid}


def test_post_with_invalid_weekday_returns_400_and_inserts_no_row(client, tmp_path) -> None:
    response = client.post("/commutes", data=_valid_form() | {"weekdays": "9"}, follow_redirects=False)

    assert response.status_code == 400
    assert "<form" in response.text

    conn = db.connect(tmp_path / "pendel.db")
    try:
        count = conn.execute("SELECT COUNT(*) FROM commutes").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_post_with_negative_delay_threshold_returns_400_and_inserts_no_row(client, tmp_path) -> None:
    form = _valid_form() | {"weekdays": "0", "delay_threshold_min": "-5"}

    response = client.post("/commutes", data=form, follow_redirects=False)

    assert response.status_code == 400
    assert "<form" in response.text

    conn = db.connect(tmp_path / "pendel.db")
    try:
        count = conn.execute("SELECT COUNT(*) FROM commutes").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_post_with_window_end_before_window_start_returns_400_and_inserts_no_row(client, tmp_path) -> None:
    form = _valid_form() | {"weekdays": "0", "window_start": "08:00", "window_end": "07:30"}

    response = client.post("/commutes", data=form, follow_redirects=False)

    assert response.status_code == 400
    assert "<form" in response.text

    conn = db.connect(tmp_path / "pendel.db")
    try:
        count = conn.execute("SELECT COUNT(*) FROM commutes").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_post_with_empty_lines_returns_400_and_inserts_no_row_in_de_and_en(client, tmp_path) -> None:
    form = _valid_form() | {"weekdays": "0", "lines": ""}

    response_de = client.post("/commutes", data=form, follow_redirects=False)
    assert response_de.status_code == 400

    response_en = client.post("/commutes?lang=en", data=form, follow_redirects=False)
    assert response_en.status_code == 400
    assert response_de.text != response_en.text

    conn = db.connect(tmp_path / "pendel.db")
    try:
        count = conn.execute("SELECT COUNT(*) FROM commutes").fetchone()[0]
    finally:
        conn.close()
    assert count == 0

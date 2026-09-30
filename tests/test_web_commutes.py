"""HTTP-level tests for GET/POST /commutes/new (charter G1): setup screen 3
picks lines from checkboxes built from the origin's own departures, never a
typed stop id or line name.

Tests are offline and replay tests/fixtures/hafas/recorded/departures_undisturbed.json
via httpx.MockTransport; the DB dependency is exercised against a real,
migrated SQLite file under `tmp_path` via PENDEL_DATA_DIR.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pendel import db
from pendel.app import app, get_hafas_client, get_now
from pendel.commute import Commute
from pendel.hafas import HafasClient, HafasError

_RECORDED_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "hafas" / "recorded"
_UNDISTURBED = json.loads((_RECORDED_FIXTURES_DIR / "departures_undisturbed.json").read_text())
_LOCATIONS_ALEXANDERPLATZ = json.loads(
    (_RECORDED_FIXTURES_DIR / "locations_alexanderplatz.json").read_text()
)
_LOCATIONS_OSTKREUZ = json.loads((_RECORDED_FIXTURES_DIR / "locations_ostkreuz.json").read_text())

# Matches the fixture's recording instant (2026-09-30, a Wednesday, no DST edge nearby).
_NOW = dt.datetime(2026, 9, 30, 6, 50, tzinfo=dt.timezone.utc)

_ORIGIN_STOP_ID = "900100003"
_ORIGIN_NAME = "S+U Alexanderplatz Bhf (Berlin)"
_DESTINATION_STOP_ID = "900120003"
_DESTINATION_NAME = "S Ostkreuz Bhf (Berlin)"

_FIXED_WIDTH_RE = re.compile(r"(?<!-)width\s*:\s*(\d+)px")
_NINE_DIGIT_RE = re.compile(r"\b\d{9}\b")


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
    app.dependency_overrides.clear()


def _undisturbed_hafas_client() -> HafasClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_UNDISTURBED)

    return HafasClient(httpx.Client(transport=httpx.MockTransport(handler)))


class _FailingHafasClient:
    def departures(self, stop_id: str, when: dt.datetime, duration: int):
        raise HafasError("boom")


class _EmptyHafasClient:
    def departures(self, stop_id: str, when: dt.datetime, duration: int):
        return {"departures": []}


def _override_undisturbed() -> None:
    app.dependency_overrides[get_hafas_client] = _undisturbed_hafas_client
    app.dependency_overrides[get_now] = lambda: _NOW


def _stop_params() -> dict[str, str]:
    return {
        "origin_stop_id": _ORIGIN_STOP_ID,
        "origin_name": _ORIGIN_NAME,
        "destination_stop_id": _DESTINATION_STOP_ID,
        "destination_name": _DESTINATION_NAME,
    }


def _valid_form() -> dict[str, str]:
    return _stop_params() | {
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


def test_commutes_new_form_without_all_four_stop_params_links_back_to_stops_without_calling_hafas(
    client,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HAFAS must not be called without all four stop params")

    app.dependency_overrides[get_hafas_client] = lambda: HafasClient(
        httpx.Client(transport=httpx.MockTransport(handler))
    )

    response = client.get("/commutes/new")

    assert response.status_code == 200
    assert 'href="/stops"' in response.text
    assert "<form" not in response.text


@pytest.mark.parametrize("missing", ["origin_stop_id", "origin_name", "destination_stop_id", "destination_name"])
def test_commutes_new_form_requires_every_one_of_the_four_stop_params(client, missing) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HAFAS must not be called with a stop param missing")

    app.dependency_overrides[get_hafas_client] = lambda: HafasClient(
        httpx.Client(transport=httpx.MockTransport(handler))
    )
    params = _stop_params()
    del params[missing]

    response = client.get("/commutes/new", params=params)

    assert response.status_code == 200
    assert 'href="/stops"' in response.text


def test_commutes_new_form_renders_line_checkboxes_from_origin_departures(client) -> None:
    _override_undisturbed()

    response = client.get("/commutes/new", params=_stop_params())

    assert response.status_code == 200
    assert '<input type="checkbox" name="lines" value="S3"' in response.text
    assert '<input type="checkbox" name="lines" value="S5"' in response.text
    assert f'name="origin_stop_id" value="{_ORIGIN_STOP_ID}"' in response.text
    assert f'name="destination_stop_id" value="{_DESTINATION_STOP_ID}"' in response.text
    assert _ORIGIN_NAME in response.text
    assert _DESTINATION_NAME in response.text
    assert 'name="lines" type="text"' not in response.text
    assert '<input type="text" id="lines"' not in response.text


def test_commutes_new_form_renders_in_german_and_english(client) -> None:
    _override_undisturbed()

    response_de = client.get("/commutes/new", params=_stop_params())
    assert response_de.status_code == 200
    assert "<form" in response_de.text

    response_en = client.get("/commutes/new", params=_stop_params() | {"lang": "en"})
    assert response_en.status_code == 200
    assert "<form" in response_en.text
    assert response_de.text != response_en.text


def test_commutes_new_form_defaults_to_mon_fri_and_5_minute_delay(client) -> None:
    _override_undisturbed()

    response = client.get("/commutes/new", params=_stop_params())

    assert response.status_code == 200
    for day in ("0", "1", "2", "3", "4"):
        assert f'value="{day}" checked' in response.text
    for day in ("5", "6"):
        assert f'value="{day}" checked' not in response.text
    assert 'name="delay_threshold_min" value="5"' in response.text


def test_commutes_new_form_default_window_starts_at_next_half_hour(client) -> None:
    app.dependency_overrides[get_hafas_client] = _undisturbed_hafas_client
    app.dependency_overrides[get_now] = lambda: dt.datetime(
        2026, 9, 30, 5, 10, tzinfo=dt.timezone.utc
    )  # 07:10 CEST

    response = client.get("/commutes/new", params=_stop_params())

    assert response.status_code == 200
    assert 'id="window_start" name="window_start" value="07:30"' in response.text
    assert 'id="window_end" name="window_end" value="08:00"' in response.text


def test_commutes_new_form_default_window_crosses_midnight_when_start_is_23_30(client) -> None:
    app.dependency_overrides[get_hafas_client] = _undisturbed_hafas_client
    app.dependency_overrides[get_now] = lambda: dt.datetime(
        2026, 9, 30, 21, 10, tzinfo=dt.timezone.utc
    )  # 23:10 CEST

    response = client.get("/commutes/new", params=_stop_params())

    assert response.status_code == 200
    assert 'id="window_start" name="window_start" value="23:30"' in response.text
    assert 'id="window_end" name="window_end" value="00:00"' in response.text


def test_commutes_new_form_default_window_wraps_past_midnight(client) -> None:
    app.dependency_overrides[get_hafas_client] = _undisturbed_hafas_client
    app.dependency_overrides[get_now] = lambda: dt.datetime(
        2026, 9, 30, 21, 45, tzinfo=dt.timezone.utc
    )  # 23:45 CEST

    response = client.get("/commutes/new", params=_stop_params())

    assert response.status_code == 200
    assert 'id="window_start" name="window_start" value="00:00"' in response.text
    assert 'id="window_end" name="window_end" value="00:30"' in response.text


def test_commutes_new_form_hafas_error_shows_alert_and_retry_link_never_a_text_field(client) -> None:
    app.dependency_overrides[get_hafas_client] = lambda: _FailingHafasClient()
    app.dependency_overrides[get_now] = lambda: _NOW

    response = client.get("/commutes/new", params=_stop_params())

    assert response.status_code == 503
    assert 'role="alert"' in response.text
    assert "/commutes/new?" in response.text
    assert _ORIGIN_STOP_ID in response.text
    assert "<form" not in response.text
    assert 'type="text"' not in response.text


def test_commutes_new_form_no_lines_shows_alert_and_retry_link(client) -> None:
    app.dependency_overrides[get_hafas_client] = lambda: _EmptyHafasClient()
    app.dependency_overrides[get_now] = lambda: _NOW

    response = client.get("/commutes/new", params=_stop_params())

    assert response.status_code == 503
    assert 'role="alert"' in response.text
    assert "<form" not in response.text


def test_commutes_new_form_has_viewport_meta_and_no_wide_fixed_widths(client) -> None:
    _override_undisturbed()

    response = client.get("/commutes/new", params=_stop_params())

    assert 'name="viewport" content="width=device-width, initial-scale=1"' in response.text
    _assert_no_wide_fixed_widths(response.text)


def test_valid_post_redirects_sets_cookie_and_inserts_one_row(client, tmp_path) -> None:
    _override_undisturbed()

    response = client.post(
        "/commutes", data=_valid_form() | {"weekdays": "0", "lines": ["S3", "S5"]}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    uid = response.cookies.get("uid")
    assert uid

    rows = _rows_for_only_user(tmp_path)
    assert len(rows) == 1
    _, commute = rows[0]
    assert commute.origin_stop_id == _ORIGIN_STOP_ID
    assert commute.destination_stop_id == _DESTINATION_STOP_ID
    assert commute.origin_name == _ORIGIN_NAME
    assert commute.destination_name == _DESTINATION_NAME
    assert commute.lines == frozenset({"S3", "S5"})
    assert commute.weekdays == frozenset({0})


def test_valid_post_with_window_crossing_midnight_saves_the_commute(client, tmp_path) -> None:
    _override_undisturbed()

    response = client.post(
        "/commutes",
        data=_valid_form()
        | {"weekdays": "5", "lines": ["S3"], "window_start": "23:30", "window_end": "00:30"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    rows = _rows_for_only_user(tmp_path)
    assert len(rows) == 1
    _, commute = rows[0]
    assert commute.window_start == dt.time(23, 30)
    assert commute.window_end == dt.time(0, 30)


def test_second_post_with_same_cookie_adds_second_commute_to_same_user(client, tmp_path) -> None:
    _override_undisturbed()

    first = client.post(
        "/commutes", data=_valid_form() | {"weekdays": "0", "lines": ["S3"]}, follow_redirects=False
    )
    assert first.status_code == 303

    second = client.post(
        "/commutes",
        data=_valid_form() | {"weekdays": ["1", "2"], "lines": ["S5"]},
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
    _override_undisturbed()
    client.cookies.set("uid", "does-not-exist")

    response = client.post(
        "/commutes", data=_valid_form() | {"weekdays": "0", "lines": ["S3"]}, follow_redirects=False
    )

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
    _override_undisturbed()

    response = client.post(
        "/commutes", data=_valid_form() | {"weekdays": "9", "lines": ["S3"]}, follow_redirects=False
    )

    assert response.status_code == 400
    assert "<form" in response.text

    conn = db.connect(tmp_path / "pendel.db")
    try:
        count = conn.execute("SELECT COUNT(*) FROM commutes").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_post_with_negative_delay_threshold_returns_400_and_inserts_no_row(client, tmp_path) -> None:
    _override_undisturbed()
    form = _valid_form() | {"weekdays": "0", "lines": ["S3"], "delay_threshold_min": "-5"}

    response = client.post("/commutes", data=form, follow_redirects=False)

    assert response.status_code == 400
    assert "<form" in response.text

    conn = db.connect(tmp_path / "pendel.db")
    try:
        count = conn.execute("SELECT COUNT(*) FROM commutes").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_post_with_malformed_window_start_returns_400_and_inserts_no_row(client, tmp_path) -> None:
    _override_undisturbed()
    form = _valid_form() | {
        "weekdays": "0",
        "lines": ["S3"],
        "window_start": "25:00",
        "window_end": "07:30",
    }

    response = client.post("/commutes", data=form, follow_redirects=False)

    assert response.status_code == 400
    assert "<form" in response.text

    conn = db.connect(tmp_path / "pendel.db")
    try:
        count = conn.execute("SELECT COUNT(*) FROM commutes").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_post_with_no_lines_selected_returns_400_and_inserts_no_row_in_de_and_en(client, tmp_path) -> None:
    _override_undisturbed()
    form = _valid_form() | {"weekdays": "0"}

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


def test_post_re_renders_checked_lines_and_re_fetches_choices_on_validation_error(client, tmp_path) -> None:
    _override_undisturbed()
    form = _valid_form() | {"weekdays": "9", "lines": ["S3"]}

    response = client.post("/commutes", data=form, follow_redirects=False)

    assert response.status_code == 400
    assert '<input type="checkbox" name="lines" value="S3" checked' in response.text
    assert '<input type="checkbox" name="lines" value="S5"' in response.text


def _walk_hafas_client() -> HafasClient:
    """Replays /locations by the `query` param (Alexanderplatz/Ostkreuz) and
    /stops/<origin>/departures, for test_setup_flow_* below: the full G1
    walk over recorded fixtures, never a stop id or line name typed."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/locations":
            query = request.url.params.get("query", "")
            if query == "Alexanderplatz":
                return httpx.Response(200, json=_LOCATIONS_ALEXANDERPLATZ)
            if query == "Ostkreuz":
                return httpx.Response(200, json=_LOCATIONS_OSTKREUZ)
            raise AssertionError(f"unexpected /locations query {query!r}")
        if path == f"/stops/{_ORIGIN_STOP_ID}/departures":
            return httpx.Response(200, json=_UNDISTURBED)
        raise AssertionError(f"unexpected request path {path!r}")

    return HafasClient(httpx.Client(transport=httpx.MockTransport(handler)))


def _href_for_stop(page_html: str, stop_id: str) -> str:
    match = re.search(rf'<li data-stop-id="{re.escape(stop_id)}"><a href="([^"]+)"', page_html)
    assert match, f"no result link for stop {stop_id} in:\n{page_html}"
    return html.unescape(match.group(1))


def test_setup_flow_walks_search_origin_search_destination_pick_lines_and_save(
    client, tmp_path
) -> None:
    """G1's definition of done, end to end: the test types only the two
    search strings, follows result hrefs parsed from the HTML, and submits
    screen 3's own hidden/default inputs plus ticked checkboxes -- never a
    typed stop id or line name."""
    app.dependency_overrides[get_hafas_client] = _walk_hafas_client
    app.dependency_overrides[get_now] = lambda: _NOW

    # Screen 1: search origin by name.
    screen1 = client.get("/stops", params={"q": "Alexanderplatz"})
    assert screen1.status_code == 200
    assert "<script" not in screen1.text
    origin_href = _href_for_stop(screen1.text, _ORIGIN_STOP_ID)

    # Screen 2: search destination by name, carrying the origin along.
    screen2 = client.get(f"{origin_href}&q=Ostkreuz")
    assert screen2.status_code == 200
    assert "<script" not in screen2.text
    destination_href = _href_for_stop(screen2.text, _DESTINATION_STOP_ID)

    # Screen 3: pick lines from checkboxes built from the origin's departures.
    screen3 = client.get(destination_href)
    assert screen3.status_code == 200
    assert "<script" not in screen3.text
    assert "<form" in screen3.text
    assert _ORIGIN_NAME in screen3.text
    assert _DESTINATION_NAME in screen3.text

    checkbox_values = set(
        re.findall(r'<input type="checkbox" name="lines" value="([^"]+)"', screen3.text)
    )
    expected_lines = {
        departure["line"]["name"]
        for departure in _UNDISTURBED["departures"]
        if departure.get("line", {}).get("name")
    }
    assert checkbox_values == expected_lines
    assert {"S3", "S5"} <= checkbox_values

    hidden_inputs = dict(
        re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', screen3.text)
    )
    assert hidden_inputs["origin_stop_id"] == _ORIGIN_STOP_ID
    assert hidden_inputs["destination_stop_id"] == _DESTINATION_STOP_ID

    default_weekdays = set(
        re.findall(r'<input type="checkbox" name="weekdays" value="([^"]+)" checked', screen3.text)
    )
    assert default_weekdays == {"0", "1", "2", "3", "4"}

    window_start = re.search(
        r'id="window_start" name="window_start" value="([^"]+)"', screen3.text
    ).group(1)
    window_end = re.search(
        r'id="window_end" name="window_end" value="([^"]+)"', screen3.text
    ).group(1)
    delay_threshold_min = re.search(
        r'id="delay_threshold_min" name="delay_threshold_min" value="([^"]+)"', screen3.text
    ).group(1)

    # The form the test submits is built only from the page's hidden and
    # default inputs plus two ticked checkboxes -- no stop id or line name
    # is typed anywhere in this test.
    form_data = dict(hidden_inputs)
    form_data["weekdays"] = sorted(default_weekdays)
    form_data["lines"] = ["S3", "S5"]
    form_data["window_start"] = window_start
    form_data["window_end"] = window_end
    form_data["delay_threshold_min"] = delay_threshold_min

    response = client.post("/commutes", data=form_data, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"

    rows = _rows_for_only_user(tmp_path)
    assert len(rows) == 1
    _, commute = rows[0]
    assert commute.origin_stop_id == _ORIGIN_STOP_ID
    assert commute.destination_stop_id == _DESTINATION_STOP_ID
    assert commute.origin_name == _ORIGIN_NAME
    assert commute.destination_name == _DESTINATION_NAME
    assert commute.lines == frozenset({"S3", "S5"})
    assert commute.weekdays == frozenset({0, 1, 2, 3, 4})
    assert commute.delay_threshold_min == 5


# GET /commutes (charter G2): "my commutes" lists every saved commute by
# name, lines, days and window. Seeding goes straight through db.add_commute
# against the same PENDEL_DATA_DIR-backed file the app uses, so these tests
# never depend on HAFAS or on the /commutes/new flow above.


def _seed_commutes(tmp_path: Path, commutes: list[Commute]) -> str:
    """Create one fresh user, save `commutes` for it and return its uid."""
    conn = db.connect(tmp_path / "pendel.db")
    try:
        uid = db.create_user(conn)
        for commute in commutes:
            db.add_commute(conn, uid, commute)
        return uid
    finally:
        conn.close()


def test_commutes_page_lists_two_commutes_by_name_lines_days_and_window(client, tmp_path) -> None:
    first = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S5", "S3"}),
        weekdays=frozenset({0, 2, 4}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    second = Commute(
        origin_stop_id="900000001",
        destination_stop_id="900000002",
        lines=frozenset({"S7"}),
        weekdays=frozenset({5, 6}),
        window_start=dt.time(9, 0),
        window_end=dt.time(9, 30),
        origin_name="Origin Two",
        destination_name="Destination Two",
    )
    uid = _seed_commutes(tmp_path, [first, second])
    client.cookies.set("uid", uid)

    response_de = client.get("/commutes")
    assert response_de.status_code == 200
    assert "Meine Verbindungen" in response_de.text
    assert f"{_ORIGIN_NAME} → {_DESTINATION_NAME}" in response_de.text
    assert "S3, S5" in response_de.text
    assert "Mo, Mi, Fr" in response_de.text
    assert "07:30–08:00" in response_de.text
    assert "Origin Two → Destination Two" in response_de.text
    assert "S7" in response_de.text
    assert "Sa, So" in response_de.text
    assert "09:00–09:30" in response_de.text

    response_en = client.get("/commutes?lang=en")
    assert response_en.status_code == 200
    assert "My commutes" in response_en.text
    assert "Mon, Wed, Fri" in response_en.text
    assert "Sat, Sun" in response_en.text


def test_commutes_page_hides_other_users_commute(client, tmp_path) -> None:
    mine = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    theirs = Commute(
        origin_stop_id="900000001",
        destination_stop_id="900000002",
        lines=frozenset({"U1"}),
        weekdays=frozenset({1}),
        window_start=dt.time(9, 0),
        window_end=dt.time(9, 30),
        origin_name="Other Origin",
        destination_name="Other Destination",
    )
    my_uid = _seed_commutes(tmp_path, [mine])
    _seed_commutes(tmp_path, [theirs])
    client.cookies.set("uid", my_uid)

    response = client.get("/commutes")

    assert response.status_code == 200
    assert f"{_ORIGIN_NAME} → {_DESTINATION_NAME}" in response.text
    assert "Other Origin → Other Destination" not in response.text
    assert "U1" not in response.text


def test_commutes_page_without_user_shows_empty_state_de_and_en(client) -> None:
    response_de = client.get("/commutes")
    assert response_de.status_code == 200
    assert "Meine Verbindungen" in response_de.text
    assert "Du hast noch keine Verbindung gespeichert." in response_de.text
    assert 'href="/stops"' in response_de.text

    response_en = client.get("/commutes?lang=en")
    assert response_en.status_code == 200
    assert "My commutes" in response_en.text
    assert "You have not saved a commute yet." in response_en.text
    assert 'href="/stops"' in response_en.text


def test_commutes_page_with_user_but_no_commutes_shows_empty_state(client, tmp_path) -> None:
    uid = _seed_commutes(tmp_path, [])
    client.cookies.set("uid", uid)

    response = client.get("/commutes")

    assert response.status_code == 200
    assert "Du hast noch keine Verbindung gespeichert." in response.text
    assert 'href="/stops"' in response.text


def test_commutes_page_shows_generic_label_for_empty_names_never_a_stop_id(client, tmp_path) -> None:
    nameless = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
    )
    uid = _seed_commutes(tmp_path, [nameless])
    client.cookies.set("uid", uid)

    response = client.get("/commutes")

    assert response.status_code == 200
    assert "Gespeicherte Verbindung" in response.text
    assert _ORIGIN_STOP_ID not in response.text
    assert _DESTINATION_STOP_ID not in response.text
    assert _NINE_DIGIT_RE.search(response.text) is None


def test_commutes_page_shows_paused_label(client, tmp_path) -> None:
    active = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    paused = Commute(
        origin_stop_id="900000001",
        destination_stop_id="900000002",
        lines=frozenset({"S7"}),
        weekdays=frozenset({1}),
        window_start=dt.time(9, 0),
        window_end=dt.time(9, 30),
        origin_name="Origin Two",
        destination_name="Destination Two",
        paused=True,
    )
    uid = _seed_commutes(tmp_path, [active, paused])
    client.cookies.set("uid", uid)

    response_de = client.get("/commutes")
    assert response_de.status_code == 200
    assert response_de.text.count("Pausiert") == 1

    response_en = client.get("/commutes?lang=en")
    assert response_en.status_code == 200
    assert response_en.text.count("Paused") == 1


# POST /commutes/{id}/pause, /resume and /delete (charter G2): each saved
# commute can be paused/resumed or deleted individually via plain POST
# forms, scoped to the uid cookie's user, never another user's rows.


def _commute_id_for(tmp_path: Path, uid: str, index: int = 0) -> int:
    conn = db.connect(tmp_path / "pendel.db")
    try:
        rows = db.list_commutes(conn, uid)
    finally:
        conn.close()
    return rows[index][0]


def test_pause_then_resume_flips_paused_flag_and_page_label(client, tmp_path) -> None:
    commute = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    uid = _seed_commutes(tmp_path, [commute])
    client.cookies.set("uid", uid)
    commute_id = _commute_id_for(tmp_path, uid)

    pause_response = client.post(f"/commutes/{commute_id}/pause", follow_redirects=False)
    assert pause_response.status_code == 303
    assert pause_response.headers["location"] == "/commutes"

    conn = db.connect(tmp_path / "pendel.db")
    try:
        (_id, paused_commute) = db.list_commutes(conn, uid)[0]
    finally:
        conn.close()
    assert paused_commute.paused is True

    page = client.get("/commutes")
    assert page.text.count("Pausiert") == 1
    assert "Fortsetzen" in page.text

    resume_response = client.post(f"/commutes/{commute_id}/resume", follow_redirects=False)
    assert resume_response.status_code == 303
    assert resume_response.headers["location"] == "/commutes"

    conn = db.connect(tmp_path / "pendel.db")
    try:
        (_id, resumed_commute) = db.list_commutes(conn, uid)[0]
    finally:
        conn.close()
    assert resumed_commute.paused is False

    page_after_resume = client.get("/commutes")
    assert "Pausiert" not in page_after_resume.text
    assert "Pausieren" in page_after_resume.text


def test_delete_one_commute_leaves_the_other_two(client, tmp_path) -> None:
    first = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name="Origin One",
        destination_name="Destination One",
    )
    second = Commute(
        origin_stop_id="900000001",
        destination_stop_id="900000002",
        lines=frozenset({"S5"}),
        weekdays=frozenset({1}),
        window_start=dt.time(8, 0),
        window_end=dt.time(8, 30),
        origin_name="Origin Two",
        destination_name="Destination Two",
    )
    third = Commute(
        origin_stop_id="900000003",
        destination_stop_id="900000004",
        lines=frozenset({"S7"}),
        weekdays=frozenset({2}),
        window_start=dt.time(9, 0),
        window_end=dt.time(9, 30),
        origin_name="Origin Three",
        destination_name="Destination Three",
    )
    uid = _seed_commutes(tmp_path, [first, second, third])
    client.cookies.set("uid", uid)
    target_id = _commute_id_for(tmp_path, uid, index=1)

    response = client.post(f"/commutes/{target_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/commutes"

    conn = db.connect(tmp_path / "pendel.db")
    try:
        remaining = db.list_commutes(conn, uid)
    finally:
        conn.close()
    assert len(remaining) == 2
    assert target_id not in {row_id for row_id, _commute in remaining}

    page = client.get("/commutes")
    assert "Origin One → Destination One" in page.text
    assert "Origin Three → Destination Three" in page.text
    assert "Origin Two → Destination Two" not in page.text


def test_pause_and_delete_another_users_commute_does_nothing(client, tmp_path) -> None:
    mine = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    theirs = Commute(
        origin_stop_id="900000001",
        destination_stop_id="900000002",
        lines=frozenset({"U1"}),
        weekdays=frozenset({1}),
        window_start=dt.time(9, 0),
        window_end=dt.time(9, 30),
        origin_name="Other Origin",
        destination_name="Other Destination",
    )
    my_uid = _seed_commutes(tmp_path, [mine])
    their_uid = _seed_commutes(tmp_path, [theirs])
    client.cookies.set("uid", my_uid)
    their_commute_id = _commute_id_for(tmp_path, their_uid)

    pause_response = client.post(f"/commutes/{their_commute_id}/pause", follow_redirects=False)
    assert pause_response.status_code == 303

    delete_response = client.post(f"/commutes/{their_commute_id}/delete", follow_redirects=False)
    assert delete_response.status_code == 303

    conn = db.connect(tmp_path / "pendel.db")
    try:
        their_rows = db.list_commutes(conn, their_uid)
    finally:
        conn.close()
    assert len(their_rows) == 1
    assert their_rows[0][1].paused is False


def test_pause_and_delete_without_uid_cookie_touches_nothing(client, tmp_path) -> None:
    commute = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    uid = _seed_commutes(tmp_path, [commute])
    commute_id = _commute_id_for(tmp_path, uid)

    pause_response = client.post(f"/commutes/{commute_id}/pause", follow_redirects=False)
    assert pause_response.status_code == 303

    delete_response = client.post(f"/commutes/{commute_id}/delete", follow_redirects=False)
    assert delete_response.status_code == 303

    conn = db.connect(tmp_path / "pendel.db")
    try:
        rows = db.list_commutes(conn, uid)
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][1].paused is False


def test_pause_resume_delete_unknown_commute_id_redirects_without_error(client, tmp_path) -> None:
    commute = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    uid = _seed_commutes(tmp_path, [commute])
    client.cookies.set("uid", uid)

    for action in ("pause", "resume", "delete"):
        response = client.post(f"/commutes/999999/{action}", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/commutes"

    conn = db.connect(tmp_path / "pendel.db")
    try:
        rows = db.list_commutes(conn, uid)
    finally:
        conn.close()
    assert len(rows) == 1


def test_commutes_page_pause_and_delete_buttons_have_named_aria_labels_de_and_en(
    client, tmp_path
) -> None:
    commute = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    uid = _seed_commutes(tmp_path, [commute])
    client.cookies.set("uid", uid)
    commute_id = _commute_id_for(tmp_path, uid)

    response_de = client.get("/commutes")
    assert response_de.status_code == 200
    assert f'action="/commutes/{commute_id}/pause"' in response_de.text
    assert f'action="/commutes/{commute_id}/delete"' in response_de.text
    assert f'aria-label="{_ORIGIN_NAME} → {_DESTINATION_NAME} pausieren"' in response_de.text
    assert f'aria-label="{_ORIGIN_NAME} → {_DESTINATION_NAME} löschen"' in response_de.text
    assert "<form" in response_de.text
    assert "<script" not in response_de.text

    response_en = client.get("/commutes?lang=en")
    assert response_en.status_code == 200
    assert f'aria-label="Pause {_ORIGIN_NAME} → {_DESTINATION_NAME}"' in response_en.text
    assert f'aria-label="Delete {_ORIGIN_NAME} → {_DESTINATION_NAME}"' in response_en.text


# GET/POST /commutes/{id}/edit and /commutes/{id} (charter G2, T-0050): a
# saved commute's lines, weekdays, window and delay threshold can be edited
# on its own; the saved origin/destination (ids, names) and the paused flag
# are kept as they are.


def test_edit_form_prefills_saved_lines_weekdays_window_and_delay_de_and_en(client, tmp_path) -> None:
    _override_undisturbed()
    commute = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({1, 3}),
        window_start=dt.time(8, 15),
        window_end=dt.time(8, 45),
        delay_threshold_min=7,
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    uid = _seed_commutes(tmp_path, [commute])
    client.cookies.set("uid", uid)
    commute_id = _commute_id_for(tmp_path, uid)

    response_de = client.get(f"/commutes/{commute_id}/edit")
    assert response_de.status_code == 200
    assert '<input type="checkbox" name="lines" value="S3" checked' in response_de.text
    assert '<input type="checkbox" name="weekdays" value="1" checked' in response_de.text
    assert '<input type="checkbox" name="weekdays" value="3" checked' in response_de.text
    assert '<input type="checkbox" name="weekdays" value="0" checked' not in response_de.text
    assert 'id="window_start" name="window_start" value="08:15"' in response_de.text
    assert 'id="window_end" name="window_end" value="08:45"' in response_de.text
    assert 'name="delay_threshold_min" value="7"' in response_de.text
    assert f'action="/commutes/{commute_id}"' in response_de.text
    assert _NINE_DIGIT_RE.search(response_de.text) is None

    response_en = client.get(f"/commutes/{commute_id}/edit?lang=en")
    assert response_en.status_code == 200
    assert '<input type="checkbox" name="lines" value="S3" checked' in response_en.text
    assert response_de.text != response_en.text
    assert _NINE_DIGIT_RE.search(response_en.text) is None


def test_edit_form_shows_saved_line_missing_from_todays_departures_as_checked(client, tmp_path) -> None:
    _override_undisturbed()
    commute = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S99"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    uid = _seed_commutes(tmp_path, [commute])
    client.cookies.set("uid", uid)
    commute_id = _commute_id_for(tmp_path, uid)

    response = client.get(f"/commutes/{commute_id}/edit")

    assert response.status_code == 200
    assert '<input type="checkbox" name="lines" value="S99" checked' in response.text


def test_valid_edit_changes_only_that_commute_and_keeps_paused_flag_and_stop_names(client, tmp_path) -> None:
    _override_undisturbed()
    target = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        delay_threshold_min=5,
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
        paused=True,
    )
    other = Commute(
        origin_stop_id="900000001",
        destination_stop_id="900000002",
        lines=frozenset({"S7"}),
        weekdays=frozenset({1}),
        window_start=dt.time(9, 0),
        window_end=dt.time(9, 30),
        origin_name="Origin Two",
        destination_name="Destination Two",
    )
    uid = _seed_commutes(tmp_path, [target, other])
    client.cookies.set("uid", uid)
    target_id = _commute_id_for(tmp_path, uid, index=0)
    other_id = _commute_id_for(tmp_path, uid, index=1)

    response = client.post(
        f"/commutes/{target_id}",
        data={
            "lines": ["S5"],
            "weekdays": ["1", "2"],
            "window_start": "18:00",
            "window_end": "18:30",
            "delay_threshold_min": "10",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/commutes"

    conn = db.connect(tmp_path / "pendel.db")
    try:
        rows = dict(db.list_commutes(conn, uid))
    finally:
        conn.close()

    updated = rows[target_id]
    assert updated.lines == frozenset({"S5"})
    assert updated.weekdays == frozenset({1, 2})
    assert updated.window_start == dt.time(18, 0)
    assert updated.window_end == dt.time(18, 30)
    assert updated.delay_threshold_min == 10
    assert updated.paused is True
    assert updated.origin_stop_id == _ORIGIN_STOP_ID
    assert updated.destination_stop_id == _DESTINATION_STOP_ID
    assert updated.origin_name == _ORIGIN_NAME
    assert updated.destination_name == _DESTINATION_NAME

    unchanged = rows[other_id]
    assert unchanged.lines == frozenset({"S7"})
    assert unchanged.weekdays == frozenset({1})
    assert unchanged.window_start == dt.time(9, 0)


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"weekdays": ["9"]},
        {"lines": []},
        {"window_start": "25:00"},
    ],
)
def test_invalid_edit_returns_400_with_alert_and_changes_nothing(client, tmp_path, invalid_fields) -> None:
    _override_undisturbed()
    commute = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        delay_threshold_min=5,
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    uid = _seed_commutes(tmp_path, [commute])
    client.cookies.set("uid", uid)
    commute_id = _commute_id_for(tmp_path, uid)

    form = {
        "lines": ["S3"],
        "weekdays": ["0"],
        "window_start": "07:30",
        "window_end": "08:00",
        "delay_threshold_min": "5",
    } | invalid_fields

    response = client.post(f"/commutes/{commute_id}", data=form, follow_redirects=False)

    assert response.status_code == 400
    assert 'role="alert"' in response.text
    assert _NINE_DIGIT_RE.search(response.text) is None

    conn = db.connect(tmp_path / "pendel.db")
    try:
        (_id, unchanged) = db.list_commutes(conn, uid)[0]
    finally:
        conn.close()
    assert unchanged.lines == frozenset({"S3"})
    assert unchanged.weekdays == frozenset({0})
    assert unchanged.window_start == dt.time(7, 30)
    assert unchanged.window_end == dt.time(8, 0)


def test_edit_another_users_commute_unknown_id_and_missing_cookie_get_404(client, tmp_path) -> None:
    _override_undisturbed()
    mine = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    theirs = Commute(
        origin_stop_id="900000001",
        destination_stop_id="900000002",
        lines=frozenset({"U1"}),
        weekdays=frozenset({1}),
        window_start=dt.time(9, 0),
        window_end=dt.time(9, 30),
        origin_name="Other Origin",
        destination_name="Other Destination",
    )
    my_uid = _seed_commutes(tmp_path, [mine])
    their_uid = _seed_commutes(tmp_path, [theirs])
    their_commute_id = _commute_id_for(tmp_path, their_uid)

    edit_form = {
        "lines": ["U1"],
        "weekdays": ["1"],
        "window_start": "09:00",
        "window_end": "09:30",
        "delay_threshold_min": "5",
    }

    # Another user's commute.
    client.cookies.set("uid", my_uid)
    assert client.get(f"/commutes/{their_commute_id}/edit").status_code == 404
    assert client.post(f"/commutes/{their_commute_id}", data=edit_form, follow_redirects=False).status_code == 404

    # Unknown id.
    assert client.get("/commutes/999999/edit").status_code == 404
    assert client.post("/commutes/999999", data=edit_form, follow_redirects=False).status_code == 404

    # Missing cookie.
    client.cookies.clear()
    assert client.get(f"/commutes/{their_commute_id}/edit").status_code == 404
    assert client.post(f"/commutes/{their_commute_id}", data=edit_form, follow_redirects=False).status_code == 404

    conn = db.connect(tmp_path / "pendel.db")
    try:
        their_rows = db.list_commutes(conn, their_uid)
        my_rows = db.list_commutes(conn, my_uid)
    finally:
        conn.close()
    assert their_rows[0][1].lines == frozenset({"U1"})
    assert my_rows[0][1].lines == frozenset({"S3"})


def test_commutes_page_shows_edit_link(client, tmp_path) -> None:
    commute = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    uid = _seed_commutes(tmp_path, [commute])
    client.cookies.set("uid", uid)
    commute_id = _commute_id_for(tmp_path, uid)

    response_de = client.get("/commutes")
    assert response_de.status_code == 200
    assert f'href="/commutes/{commute_id}/edit"' in response_de.text
    assert "Bearbeiten" in response_de.text

    response_en = client.get("/commutes?lang=en")
    assert response_en.status_code == 200
    assert f'href="/commutes/{commute_id}/edit"' in response_en.text
    assert "Edit" in response_en.text


def test_edit_form_has_viewport_meta_and_no_wide_fixed_widths(client, tmp_path) -> None:
    _override_undisturbed()
    commute = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    uid = _seed_commutes(tmp_path, [commute])
    client.cookies.set("uid", uid)
    commute_id = _commute_id_for(tmp_path, uid)

    response = client.get(f"/commutes/{commute_id}/edit")

    assert 'name="viewport" content="width=device-width, initial-scale=1"' in response.text
    _assert_no_wide_fixed_widths(response.text)


def test_commutes_page_has_viewport_meta_and_no_wide_fixed_widths(client, tmp_path) -> None:
    seeded = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({0}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name=_ORIGIN_NAME,
        destination_name=_DESTINATION_NAME,
    )
    uid = _seed_commutes(tmp_path, [seeded])
    client.cookies.set("uid", uid)

    response = client.get("/commutes")

    assert 'name="viewport" content="width=device-width, initial-scale=1"' in response.text
    _assert_no_wide_fixed_widths(response.text)

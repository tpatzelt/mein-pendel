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

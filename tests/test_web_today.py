"""HTTP-level tests for GET /today (charter G2): "today on my route" shows
each saved, active commute's engine verdict for today.

Fixtures under tests/fixtures/hafas/web/ are hand-made and labelled synthetic
HAFAS v6 `/stops/:id/departures` responses; nothing here makes or requires a
live call (the DST-safe engine itself is already covered by test_engine.py).
"""

from __future__ import annotations

import datetime as dt
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

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "hafas" / "web"

# Monday, winter (no DST), matching the commute windows used below.
_NOW = dt.datetime(2026, 1, 5, 6, 0, tzinfo=dt.timezone.utc)

_ORIGIN_STOP_ID = "900000100001"
_DESTINATION_STOP_ID = "900000200002"

# Matches a `width: NNpx` declaration but not `max-width`/`min-width`.
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


def _commute(**overrides) -> Commute:
    defaults = dict(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S41"}),
        weekdays=frozenset({0, 1, 2, 3, 4}),  # Monday..Friday
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
    )
    defaults.update(overrides)
    return Commute(**defaults)


def _seed_commute(tmp_path, commute: Commute) -> str:
    conn = db.connect(tmp_path / "pendel.db")
    try:
        uid = db.create_user(conn)
        db.add_commute(conn, uid, commute)
        conn.commit()
    finally:
        conn.close()
    return uid


def _departures_client(fixture_name: str) -> HafasClient:
    payload = json.loads((_FIXTURES_DIR / fixture_name).read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return HafasClient(httpx.Client(transport=httpx.MockTransport(handler)))


class _FailingHafasClient:
    def departures(self, stop_id: str, when: dt.datetime, duration: int):
        raise HafasError("boom")


def _override_now(client) -> None:
    app.dependency_overrides[get_now] = lambda: _NOW


def test_today_without_cookie_shows_empty_state_and_does_not_call_hafas(client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HAFAS must not be called without a known user")

    app.dependency_overrides[get_hafas_client] = lambda: HafasClient(
        httpx.Client(transport=httpx.MockTransport(handler))
    )

    response = client.get("/today")

    assert response.status_code == 200
    assert 'href="/stops"' in response.text


def test_today_with_unknown_cookie_shows_empty_state(client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HAFAS must not be called for an unknown user")

    app.dependency_overrides[get_hafas_client] = lambda: HafasClient(
        httpx.Client(transport=httpx.MockTransport(handler))
    )
    client.cookies.set("uid", "does-not-exist")

    response = client.get("/today")

    assert response.status_code == 200
    assert 'href="/stops"' in response.text


def test_today_inactive_commute_shows_inactive_status_without_calling_hafas(
    client, tmp_path
) -> None:
    commute = _commute(weekdays=frozenset({5}))  # Saturday only; _NOW is a Monday
    uid = _seed_commute(tmp_path, commute)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HAFAS must not be called for a commute inactive today")

    _override_now(client)
    app.dependency_overrides[get_hafas_client] = lambda: HafasClient(
        httpx.Client(transport=httpx.MockTransport(handler))
    )
    client.cookies.set("uid", uid)

    response = client.get("/today")

    assert response.status_code == 200
    assert "Heute nicht aktiv" in response.text


def test_today_affected_commute_shows_reason_in_german_and_english(client, tmp_path) -> None:
    commute = _commute()
    uid = _seed_commute(tmp_path, commute)

    _override_now(client)
    app.dependency_overrides[get_hafas_client] = lambda: _departures_client(
        "synthetic_today_affected.json"
    )
    client.cookies.set("uid", uid)

    response_de = client.get("/today", params={"lang": "de"})
    assert response_de.status_code == 200
    assert "S41 fällt aus." in response_de.text

    response_en = client.get("/today", params={"lang": "en"})
    assert response_en.status_code == 200
    assert "S41 is cancelled." in response_en.text


def test_today_unaffected_commute_shows_no_disruption_text(client, tmp_path) -> None:
    commute = _commute()
    uid = _seed_commute(tmp_path, commute)

    _override_now(client)
    app.dependency_overrides[get_hafas_client] = lambda: _departures_client(
        "synthetic_today_unaffected.json"
    )
    client.cookies.set("uid", uid)

    response_de = client.get("/today", params={"lang": "de"})
    assert response_de.status_code == 200
    assert "Keine Störung auf dieser Verbindung." in response_de.text

    response_en = client.get("/today", params={"lang": "en"})
    assert response_en.status_code == 200
    assert "No disruption on this route." in response_en.text


def test_today_hafas_error_shows_unavailable_message_with_status_200(client, tmp_path) -> None:
    commute = _commute()
    uid = _seed_commute(tmp_path, commute)

    _override_now(client)
    app.dependency_overrides[get_hafas_client] = lambda: _FailingHafasClient()
    client.cookies.set("uid", uid)

    response = client.get("/today")

    assert response.status_code == 200
    assert "Traceback" not in response.text
    assert "Status gerade nicht verfügbar." in response.text


def test_today_page_has_viewport_meta_and_no_wide_fixed_widths(client, tmp_path) -> None:
    commute = _commute()
    uid = _seed_commute(tmp_path, commute)

    _override_now(client)
    app.dependency_overrides[get_hafas_client] = lambda: _departures_client(
        "synthetic_today_affected.json"
    )
    client.cookies.set("uid", uid)

    for lang in ("de", "en"):
        response = client.get("/today", params={"lang": lang})
        assert response.status_code == 200
        assert 'name="viewport" content="width=device-width, initial-scale=1"' in response.text
        _assert_no_wide_fixed_widths(response.text)


def test_home_links_to_today() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/today"' in response.text

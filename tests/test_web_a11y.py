"""HTTP-level tests for accessibility (charter G2): error messages announced
to screen readers via role="alert" and an accessible name on the footer nav.

Offline; failure/invalid-input paths mirror tests/test_web_stops.py,
tests/test_web_commutes.py and tests/test_web_notifications.py.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pendel import db
from pendel.app import app, get_hafas_client, get_now
from pendel.hafas import HafasClient, HafasError

_UNDISTURBED = json.loads(
    (Path(__file__).parent / "fixtures" / "hafas" / "recorded" / "departures_undisturbed.json").read_text()
)


class _FailingHafasClient:
    def locations(self, query: str) -> list[dict[str, str]]:
        raise HafasError("boom")


def _undisturbed_hafas_client() -> HafasClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_UNDISTURBED)

    return HafasClient(httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDEL_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _seed_user(tmp_path) -> str:
    conn = db.connect(tmp_path / "pendel.db")
    try:
        return db.create_user(conn)
    finally:
        conn.close()


def test_stops_page_hafas_error_has_role_alert() -> None:
    client = TestClient(app)
    app.dependency_overrides[get_hafas_client] = lambda: _FailingHafasClient()

    response = client.get("/stops", params={"q": "Alexanderplatz"})

    assert response.status_code == 503
    assert 'role="alert"' in response.text


def test_commutes_new_invalid_window_has_role_alert(tmp_path) -> None:
    client = TestClient(app)
    app.dependency_overrides[get_hafas_client] = _undisturbed_hafas_client
    app.dependency_overrides[get_now] = lambda: dt.datetime(2026, 9, 30, 6, 50, tzinfo=dt.timezone.utc)
    form = {
        "origin_stop_id": "900100003",
        "origin_name": "S+U Alexanderplatz Bhf (Berlin)",
        "destination_stop_id": "900120003",
        "destination_name": "S Ostkreuz Bhf (Berlin)",
        "lines": "S3",
        "weekdays": "0",
        "window_start": "08:00",
        "window_end": "07:30",
        "delay_threshold_min": "5",
    }

    response = client.post("/commutes", data=form, follow_redirects=False)

    assert response.status_code == 400
    assert 'role="alert"' in response.text


def test_notifications_invalid_ntfy_topic_has_role_alert(tmp_path) -> None:
    client = TestClient(app)
    uid = _seed_user(tmp_path)
    client.cookies.set("uid", uid)

    response = client.post("/notifications/ntfy", data={"topic": "not a valid topic!"})

    assert response.status_code == 400
    assert 'role="alert"' in response.text


def test_footer_nav_has_aria_label_in_german() -> None:
    client = TestClient(app)

    response = client.get("/", params={"lang": "de"})

    assert response.status_code == 200
    assert '<nav aria-label="Fußzeilen-Navigation">' in response.text


def test_footer_nav_has_aria_label_in_english() -> None:
    client = TestClient(app)

    response = client.get("/", params={"lang": "en"})

    assert response.status_code == 200
    assert '<nav aria-label="Footer navigation">' in response.text


def test_stops_page_without_query_has_no_role_alert() -> None:
    client = TestClient(app)

    response = client.get("/stops")

    assert response.status_code == 200
    assert 'role="alert"' not in response.text

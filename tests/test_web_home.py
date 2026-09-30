"""HTTP-level tests for GET / (charter G2): the home page must link to /stops
so a first-time visitor has a direct way into the save-commute flow.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pendel.app import app


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDEL_DATA_DIR", str(tmp_path))
    return tmp_path


def test_home_links_to_stops_in_german() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/stops"' in response.text
    assert "Haltestelle suchen" in response.text


def test_home_links_to_stops_in_english() -> None:
    client = TestClient(app)
    response = client.get("/", params={"lang": "en"})
    assert response.status_code == 200
    assert 'href="/stops"' in response.text
    assert "Search for a stop" in response.text


def test_home_stops_link_resolves_to_200() -> None:
    client = TestClient(app)
    response = client.get("/stops")
    assert response.status_code == 200

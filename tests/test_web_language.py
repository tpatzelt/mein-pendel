"""HTTP-level tests for the visible DE/EN language switch link in the
shared footer (charter G2: German and English UI, reachable without
editing the URL by hand).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pendel.app import app

client = TestClient(app)

_ROUTES = ("/", "/impressum", "/about", "/stops")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDEL_DATA_DIR", str(tmp_path))


@pytest.fixture(autouse=True)
def _clear_cookies():
    client.cookies.clear()
    yield
    client.cookies.clear()


def test_default_german_pages_link_to_english_switch() -> None:
    for path in _ROUTES:
        response = client.get(path)
        assert response.status_code == 200, path
        assert 'href="/?lang=en"' in response.text, path


def test_english_pages_link_to_german_switch_and_not_english() -> None:
    for path in _ROUTES:
        response = client.get(path, params={"lang": "en"})
        assert response.status_code == 200, path
        assert 'href="/?lang=de"' in response.text, path
        assert 'href="/?lang=en"' not in response.text, path


def test_following_switch_link_sets_cookie_and_persists_on_other_pages() -> None:
    response = client.get("/", params={"lang": "en"})
    assert response.status_code == 200
    assert client.cookies.get("lang") == "en"

    response = client.get("/about")
    assert response.status_code == 200
    assert '<html lang="en">' in response.text

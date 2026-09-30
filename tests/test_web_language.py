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


def _expected_switch_href(path: str, lang: str) -> str:
    target = "/" if path == "/" else path
    return f'href="{target}?lang={lang}"'


def test_default_german_pages_link_to_english_switch() -> None:
    for path in _ROUTES:
        response = client.get(path)
        assert response.status_code == 200, path
        assert _expected_switch_href(path, "en") in response.text, path


def test_english_pages_link_to_german_switch_and_not_english() -> None:
    for path in _ROUTES:
        response = client.get(path, params={"lang": "en"})
        assert response.status_code == 200, path
        assert _expected_switch_href(path, "de") in response.text, path
        assert _expected_switch_href(path, "en") not in response.text, path


def test_following_switch_link_sets_cookie_and_persists_on_other_pages() -> None:
    response = client.get("/", params={"lang": "en"})
    assert response.status_code == 200
    assert client.cookies.get("lang") == "en"

    response = client.get("/about")
    assert response.status_code == 200
    assert '<html lang="en">' in response.text


def test_about_page_switch_link_uses_own_path_in_english() -> None:
    response = client.get("/about", params={"lang": "en"})
    assert response.status_code == 200
    assert 'href="/about?lang=de"' in response.text


def test_today_page_with_no_user_links_to_its_own_path() -> None:
    response = client.get("/today")
    assert response.status_code == 200
    assert 'href="/today?lang=en"' in response.text


def test_invalid_post_commutes_switch_link_falls_back_to_home() -> None:
    form = {
        "origin_stop_id": "900000100001",
        "destination_stop_id": "900000200002",
        "lines": "S41, S42",
        "weekdays": "0",
        "window_start": "08:00",
        "window_end": "07:30",
        "delay_threshold_min": "5",
    }

    response = client.post("/commutes", data=form, follow_redirects=False)

    assert response.status_code == 400
    assert 'href="/?lang=en"' in response.text


def test_following_switch_link_on_about_sets_cookie_and_renders_english() -> None:
    response = client.get("/about", follow_redirects=False)
    assert response.status_code == 200
    assert 'href="/about?lang=en"' in response.text

    response = client.get("/about", params={"lang": "en"})
    assert response.status_code == 200
    assert client.cookies.get("lang") == "en"
    assert '<html lang="en">' in response.text

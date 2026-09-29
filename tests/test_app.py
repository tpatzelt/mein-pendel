"""HTTP-level tests for the FastAPI app skeleton (charter G2).

Covers /healthz, language resolution for `/`, and an HTML-structure check for
the 360px mobile layout: a viewport meta tag must be present, and neither the
rendered page nor the static CSS may set a `width` (not `max-width`/`min-width`)
above 360px, since that would break the smallest supported viewport.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from pendel.app import app

client = TestClient(app)

_STATIC_CSS = Path(__file__).parent.parent / "src" / "pendel" / "static" / "css" / "style.css"

# Matches a `width: NNpx` declaration but not `max-width`/`min-width`.
_FIXED_WIDTH_RE = re.compile(r"(?<!-)width\s*:\s*(\d+)px")


def _assert_no_wide_fixed_widths(text: str) -> None:
    for match in _FIXED_WIDTH_RE.finditer(text):
        assert int(match.group(1)) <= 360, f"fixed width above 360px found: {match.group(0)!r}"


def test_healthz_returns_ok() -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_home_renders_with_viewport_meta_and_default_german() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in response.text
    assert '<html lang="de">' in response.text
    assert "Behalte deine Verbindung im Blick." in response.text


def test_home_query_param_selects_english_and_sets_cookie() -> None:
    response = client.get("/?lang=en")

    assert response.status_code == 200
    assert '<html lang="en">' in response.text
    assert "Keep an eye on your commute." in response.text
    assert response.cookies.get("lang") == "en"


def test_home_cookie_selects_language_without_query_param() -> None:
    client.cookies.set("lang", "en")
    try:
        response = client.get("/")
    finally:
        client.cookies.clear()

    assert '<html lang="en">' in response.text


def test_home_accept_language_header_selects_english() -> None:
    response = client.get("/", headers={"Accept-Language": "en-US,en;q=0.9,de;q=0.5"})

    assert '<html lang="en">' in response.text


def test_home_html_has_no_fixed_width_above_360px() -> None:
    response = client.get("/")

    _assert_no_wide_fixed_widths(response.text)


def test_static_css_has_no_fixed_width_above_360px() -> None:
    css = _STATIC_CSS.read_text()

    _assert_no_wide_fixed_widths(css)

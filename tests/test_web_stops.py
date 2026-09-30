"""HTTP-level tests for GET /stops (charter G2): server-rendered stop search.

Fixtures under tests/fixtures/hafas/web/ are hand-made and labelled synthetic
data shaped like a HAFAS v6 /locations response; nothing here makes or
requires a live call.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pendel.app import app, get_hafas_client
from pendel.hafas import HafasClient, HafasError

client = TestClient(app)

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "hafas" / "web"

# Matches a `width: NNpx` declaration but not `max-width`/`min-width`.
_FIXED_WIDTH_RE = re.compile(r"(?<!-)width\s*:\s*(\d+)px")


def _assert_no_wide_fixed_widths(text: str) -> None:
    for match in _FIXED_WIDTH_RE.finditer(text):
        assert int(match.group(1)) <= 360, f"fixed width above 360px found: {match.group(0)!r}"


def _locations_client(fixture_name: str) -> HafasClient:
    payload = json.loads((_FIXTURES_DIR / fixture_name).read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return HafasClient(httpx.Client(transport=httpx.MockTransport(handler)))


class _FailingHafasClient:
    def locations(self, query: str) -> list[dict[str, str]]:
        raise HafasError("boom")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()
    client.cookies.clear()


def test_stops_page_without_query_does_not_call_hafas() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HAFAS must not be called without a query")

    app.dependency_overrides[get_hafas_client] = lambda: HafasClient(
        httpx.Client(transport=httpx.MockTransport(handler))
    )

    response = client.get("/stops")

    assert response.status_code == 200
    assert "<form" in response.text


def test_stops_page_with_short_query_does_not_call_hafas() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HAFAS must not be called for queries under 2 chars")

    app.dependency_overrides[get_hafas_client] = lambda: HafasClient(
        httpx.Client(transport=httpx.MockTransport(handler))
    )

    response = client.get("/stops", params={"q": "A"})

    assert response.status_code == 200


def test_stops_page_renders_stop_and_station_results_filtering_other_types() -> None:
    app.dependency_overrides[get_hafas_client] = lambda: _locations_client(
        "synthetic_locations.json"
    )

    response = client.get("/stops", params={"q": "Alexanderplatz"})

    assert response.status_code == 200
    assert "S+U Alexanderplatz" in response.text
    assert "S+U Alexanderplatz (Berlin)" in response.text
    assert "Alexanderplatz 1, 10178 Berlin" not in response.text
    assert "Alexanderplatz (POI)" not in response.text


def test_stops_page_shows_no_results_message_in_de_and_en() -> None:
    app.dependency_overrides[get_hafas_client] = lambda: _locations_client(
        "synthetic_locations_empty.json"
    )

    response_de = client.get("/stops", params={"q": "Nirgendwo"})
    assert response_de.status_code == 200
    assert "Keine Treffer." in response_de.text

    response_en = client.get("/stops", params={"q": "Nirgendwo", "lang": "en"})
    assert response_en.status_code == 200
    assert "No results." in response_en.text


def test_stops_page_hafas_error_renders_friendly_message_not_traceback() -> None:
    app.dependency_overrides[get_hafas_client] = lambda: _FailingHafasClient()

    response_de = client.get("/stops", params={"q": "Alexanderplatz", "lang": "de"})
    assert response_de.status_code == 503
    assert "Traceback" not in response_de.text
    assert "nicht verfügbar" in response_de.text

    response_en = client.get("/stops", params={"q": "Alexanderplatz", "lang": "en"})
    assert response_en.status_code == 503
    assert "temporarily unavailable" in response_en.text


def test_stops_page_hafas_error_through_real_client_and_mock_transport() -> None:
    """A real HafasClient over a MockTransport that returns HTTP 500 proves
    actual HafasError instances (not just a duck-typed fake) map to the
    friendly 503 page."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    real_client = HafasClient(httpx.Client(transport=httpx.MockTransport(handler)))
    app.dependency_overrides[get_hafas_client] = lambda: real_client

    response = client.get("/stops", params={"q": "Alexanderplatz"})

    assert response.status_code == 503
    assert "Traceback" not in response.text


def test_stops_page_has_viewport_meta_and_no_wide_fixed_widths() -> None:
    app.dependency_overrides[get_hafas_client] = lambda: _locations_client(
        "synthetic_locations.json"
    )

    for lang in ("de", "en"):
        response = client.get("/stops", params={"lang": lang, "q": "Alexanderplatz"})
        assert response.status_code == 200
        assert 'name="viewport" content="width=device-width, initial-scale=1"' in response.text
        _assert_no_wide_fixed_widths(response.text)


def test_stops_page_without_origin_links_to_commutes_new_with_origin_only() -> None:
    app.dependency_overrides[get_hafas_client] = lambda: _locations_client(
        "synthetic_locations.json"
    )

    response = client.get("/stops", params={"q": "Alexanderplatz"})

    assert response.status_code == 200
    assert 'href="/commutes/new?origin_stop_id=900000100001"' in response.text
    assert "destination_stop_id" not in response.text


def test_stops_page_with_origin_stop_id_links_to_commutes_new_with_both_ids() -> None:
    app.dependency_overrides[get_hafas_client] = lambda: _locations_client(
        "synthetic_locations.json"
    )

    response = client.get(
        "/stops", params={"q": "Alexanderplatz", "origin_stop_id": "900000999999"}
    )

    assert response.status_code == 200
    assert (
        'href="/commutes/new?origin_stop_id=900000999999&amp;destination_stop_id=900000100001"'
        in response.text
    )


def test_stops_page_with_origin_stop_id_shows_destination_hint_in_de_and_en() -> None:
    app.dependency_overrides[get_hafas_client] = lambda: _locations_client(
        "synthetic_locations.json"
    )

    response_de = client.get(
        "/stops", params={"q": "Alexanderplatz", "origin_stop_id": "900000999999"}
    )
    assert response_de.status_code == 200
    assert "Wähle jetzt die Ziel-Haltestelle." in response_de.text

    response_en = client.get(
        "/stops",
        params={"q": "Alexanderplatz", "origin_stop_id": "900000999999", "lang": "en"},
    )
    assert response_en.status_code == 200
    assert "Now choose the destination stop." in response_en.text


def test_stops_page_without_origin_shows_no_destination_hint() -> None:
    app.dependency_overrides[get_hafas_client] = lambda: _locations_client(
        "synthetic_locations.json"
    )

    response = client.get("/stops", params={"q": "Alexanderplatz"})

    assert response.status_code == 200
    assert "Wähle jetzt die Ziel-Haltestelle." not in response.text


def test_stops_page_keeps_origin_stop_id_as_hidden_input_for_repeated_search() -> None:
    app.dependency_overrides[get_hafas_client] = lambda: _locations_client(
        "synthetic_locations.json"
    )

    response = client.get(
        "/stops", params={"q": "Alexanderplatz", "origin_stop_id": "900000999999"}
    )

    assert response.status_code == 200
    assert '<input type="hidden" name="origin_stop_id" value="900000999999">' in response.text


def test_stops_page_origin_stop_id_cannot_inject_attribute_or_tag() -> None:
    app.dependency_overrides[get_hafas_client] = lambda: _locations_client(
        "synthetic_locations.json"
    )
    malicious = '900000999999"><script>alert(1)</script>&x=1'

    response = client.get(
        "/stops", params={"q": "Alexanderplatz", "origin_stop_id": malicious}
    )

    assert response.status_code == 200
    assert "<script>" not in response.text
    assert '"><script>' not in response.text
    # The hidden input's value attribute must stay a single, well-formed attribute.
    assert '<input type="hidden" name="origin_stop_id" value="900000999999&#34;' in response.text
    # The destination link must percent-encode the payload instead of splicing it in raw.
    assert "href=\"/commutes/new?origin_stop_id=900000999999%22%3E%3Cscript%3E" in response.text


def test_get_hafas_client_is_a_shared_singleton() -> None:
    """Production code must reuse one HafasClient (and its TTL cache) per
    process rather than building a fresh, empty-cache client per request."""
    try:
        first = get_hafas_client()
        second = get_hafas_client()
        assert first is second
    finally:
        get_hafas_client.cache_clear()

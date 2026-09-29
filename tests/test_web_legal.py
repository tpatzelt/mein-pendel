"""HTTP-level tests for the legal/about pages (charter G5): Impressum,
Datenschutzerklärung and About, linked from the footer of every page.

These pages carry no HAFAS traffic, so the fixture/replay plumbing used by
test_web_stops.py and test_web_today.py is not needed here; only the
existing 360px structure-test pattern is reused.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from pendel.app import app

client = TestClient(app)

_LEGAL_ROUTES = ("/impressum", "/datenschutz", "/about")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDEL_DATA_DIR", str(tmp_path))

# Matches a `width: NNpx` declaration but not `max-width`/`min-width`.
_FIXED_WIDTH_RE = re.compile(r"(?<!-)width\s*:\s*(\d+)px")


def _assert_no_wide_fixed_widths(text: str) -> None:
    for match in _FIXED_WIDTH_RE.finditer(text):
        assert int(match.group(1)) <= 360, f"fixed width above 360px found: {match.group(0)!r}"


def test_legal_routes_return_200_in_both_languages() -> None:
    for path in _LEGAL_ROUTES:
        for lang in ("de", "en"):
            response = client.get(path, params={"lang": lang})
            assert response.status_code == 200, (path, lang)


def test_legal_routes_have_viewport_meta_and_no_wide_fixed_widths() -> None:
    for path in _LEGAL_ROUTES:
        for lang in ("de", "en"):
            response = client.get(path, params={"lang": lang})
            assert (
                'name="viewport" content="width=device-width, initial-scale=1"'
                in response.text
            )
            _assert_no_wide_fixed_widths(response.text)


def test_footer_links_all_three_legal_pages_on_home_today_and_stops() -> None:
    for path in ("/", "/today", "/stops"):
        response = client.get(path)
        assert response.status_code == 200
        assert 'href="/impressum"' in response.text
        assert 'href="/datenschutz"' in response.text
        assert 'href="/about"' in response.text


def test_impressum_has_placeholders_in_both_languages() -> None:
    for lang in ("de", "en"):
        response = client.get("/impressum", params={"lang": lang})
        assert "[NAME]" in response.text
        assert "[ADRESSE]" in response.text
        assert "[E-MAIL]" in response.text


def test_impressum_cites_ddg_paragraph_5_and_not_tmg() -> None:
    for lang in ("de", "en"):
        response = client.get("/impressum", params={"lang": lang})
        assert "§ 5 DDG" in response.text
        assert "TMG" not in response.text


def test_about_states_free_and_links_kofi_placeholder() -> None:
    response_de = client.get("/about", params={"lang": "de"})
    assert "kostenlos" in response_de.text
    assert "ohne Werbung" in response_de.text
    assert "ohne Tracking" in response_de.text
    assert 'href="https://ko-fi.com/[PLACEHOLDER]"' in response_de.text

    response_en = client.get("/about", params={"lang": "en"})
    assert "free" in response_en.text
    assert "no ads" in response_en.text
    assert "no tracking" in response_en.text
    assert 'href="https://ko-fi.com/[PLACEHOLDER]"' in response_en.text


def test_datenschutz_describes_uid_cookie_as_opaque_not_non_personal() -> None:
    response_de = client.get("/datenschutz", params={"lang": "de"})
    assert "zufälligen, undurchsichtigen Kennung (uid)" in response_de.text
    assert "nicht personenbezogen" not in response_de.text

    response_en = client.get("/datenschutz", params={"lang": "en"})
    assert "random, opaque identifier (uid)" in response_en.text
    assert "non-personal" not in response_en.text


def test_datenschutz_mentions_commute_fields_and_delay_threshold() -> None:
    for lang in ("de", "en"):
        response = client.get("/datenschutz", params={"lang": lang})
        assert "delay_threshold_min" in response.text


def test_datenschutz_mentions_third_party_delivery_sentence() -> None:
    response_de = client.get("/datenschutz", params={"lang": "de"})
    assert "Drittanbieter Telegram" in response_de.text
    assert "vom Betreiber konfigurierten ntfy-Server" in response_de.text

    response_en = client.get("/datenschutz", params={"lang": "en"})
    assert "third-party services Telegram" in response_en.text
    assert "operator-configured ntfy server" in response_en.text


def test_datenschutz_ip_sentence_is_memory_only_no_db_lost_on_restart() -> None:
    response_de = client.get("/datenschutz", params={"lang": "de"})
    assert "Arbeitsspeicher des Servers" in response_de.text
    assert "nicht in der Datenbank gespeichert" in response_de.text
    assert "Neustart des Servers verloren" in response_de.text
    assert "protokolliert" not in response_de.text

    response_en = client.get("/datenschutz", params={"lang": "en"})
    assert "memory on the server" in response_en.text
    assert "not stored in the database" in response_en.text
    assert "lost at the latest when the server restarts" in response_en.text
    assert "logged" not in response_en.text


def test_datenschutz_mentions_hafas_api_and_delete_endpoint() -> None:
    response_de = client.get("/datenschutz", params={"lang": "de"})
    assert "VBB/BVG-HAFAS-REST-API" in response_de.text
    assert "POST /me/delete" in response_de.text

    response_en = client.get("/datenschutz", params={"lang": "en"})
    assert "VBB/BVG HAFAS REST API" in response_en.text
    assert "POST /me/delete" in response_en.text

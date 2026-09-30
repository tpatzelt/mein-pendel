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
_RECORDED_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "hafas" / "recorded"

# Monday, winter (no DST), matching the commute windows used below.
_NOW = dt.datetime(2026, 1, 5, 6, 0, tzinfo=dt.timezone.utc)

_ORIGIN_STOP_ID = "900000100001"
_DESTINATION_STOP_ID = "900000200002"

# Wednesday 08:00 Europe/Berlin, matching the plannedWhen times recorded in
# tests/fixtures/hafas/recorded/departures_undisturbed.json (07:xx) and
# departures_warning.json (08:06), used by the multi-status test below.
_RECORDED_NOW = dt.datetime(2026, 9, 30, 6, 0, tzinfo=dt.timezone.utc)

_ALEXANDERPLATZ_STOP_ID = "900100003"
_OSTKREUZ_STOP_ID = "900120003"
_PAUSED_STOP_ID = "900555555"
_FAILED_STOP_ID = "900666666"

# Matches a `width: NNpx` declaration but not `max-width`/`min-width`.
_FIXED_WIDTH_RE = re.compile(r"(?<!-)width\s*:\s*(\d+)px")

_DEPARTURES_BLOCK_RE = re.compile(r'<ul class="departures">.*?</ul>', re.DOTALL)


def _assert_no_wide_fixed_widths(text: str) -> None:
    for match in _FIXED_WIDTH_RE.finditer(text):
        assert int(match.group(1)) <= 360, f"fixed width above 360px found: {match.group(0)!r}"


def _departures_block(text: str) -> str:
    """The `<ul class="departures">...</ul>` markup for a card, isolated so
    assertions about it fail if the cancellation/time text only happens to
    appear elsewhere on the page (e.g. in the verdict's reason sentence)."""
    match = _DEPARTURES_BLOCK_RE.search(text)
    assert match is not None, "expected a departures list in the response"
    return match.group(0)


def _card_block(text: str, status: str) -> str:
    match = re.search(
        rf'<li data-commute-id="\d+" data-status="{status}">.*?</li>', text, re.DOTALL
    )
    assert match is not None, f"expected a card with status {status!r}"
    return match.group(0)


def _mock_client(payload: dict) -> HafasClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return HafasClient(httpx.Client(transport=httpx.MockTransport(handler)))


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
    return _seed_commutes(tmp_path, [commute])


def _seed_commutes(tmp_path, commutes: list[Commute]) -> str:
    conn = db.connect(tmp_path / "pendel.db")
    try:
        uid = db.create_user(conn)
        for commute in commutes:
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


def test_today_commute_with_window_crossing_midnight_is_active_and_fetched_with_that_window(
    client, tmp_path
) -> None:
    """Charter G2: a departure window crossing midnight (e.g. 23:30-00:30)
    that started yesterday and is still open now must still count as active
    -- not "Heute nicht aktiv" -- and the HAFAS departures call must use that
    crossing window (Saturday 23:30 through Sunday 00:30), not today's
    (Sunday's) window, so the check actually covers the ridden departure."""
    commute = _commute(
        weekdays=frozenset({5}),  # Saturday only
        window_start=dt.time(23, 30),
        window_end=dt.time(0, 30),
    )
    uid = _seed_commute(tmp_path, commute)

    now = dt.datetime(2026, 1, 3, 23, 10, tzinfo=dt.timezone.utc)  # 00:10 CET, Sunday 2026-01-04
    payload = {
        "departures": [
            {
                "tripId": "1",
                "stop": {"id": _ORIGIN_STOP_ID, "name": "Origin"},
                "when": "2026-01-04T00:20:00+01:00",
                "plannedWhen": "2026-01-04T00:20:00+01:00",
                "delay": 0,
                "cancelled": False,
                "line": {"id": "line:S41", "name": "S41", "product": "suburban"},
                "remarks": [],
            }
        ]
    }

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    app.dependency_overrides[get_now] = lambda: now
    app.dependency_overrides[get_hafas_client] = lambda: HafasClient(
        httpx.Client(transport=httpx.MockTransport(handler))
    )
    client.cookies.set("uid", uid)

    response = client.get("/today", params={"lang": "de"})

    assert response.status_code == 200
    assert "Heute nicht aktiv" not in response.text
    assert 'data-status="ok"' in response.text

    assert len(requests) == 1
    params = requests[0].url.params
    expected_start, expected_end = commute.window_bounds(dt.date(2026, 1, 3))
    assert dt.datetime.fromisoformat(params["when"]) == expected_start
    expected_duration = max(1, int((expected_end - expected_start).total_seconds() // 60) + 1)
    assert int(params["duration"]) == expected_duration

    block = _departures_block(response.text)
    assert '<time datetime="2026-01-04T00:20:00+01:00">00:20</time>' in block


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
    block_de = _departures_block(response_de.text)
    assert '<time datetime="2026-01-05T07:45:00+01:00">07:45</time>' in block_de
    assert "fällt aus" in block_de

    response_en = client.get("/today", params={"lang": "en"})
    assert response_en.status_code == 200
    assert "S41 is cancelled." in response_en.text
    block_en = _departures_block(response_en.text)
    assert '<time datetime="2026-01-05T07:45:00+01:00">07:45</time>' in block_en
    assert "cancelled" in block_en


def test_today_shows_planned_and_realtime_time_with_platform(client, tmp_path) -> None:
    """Against the recorded Ostkreuz fixture (charter G3): RB32 is planned
    17:18, delayed to real-time 17:20 (delay 120s), platform 14 on both --
    the card must show the planned time, the real-time time, the delay in
    minutes as text and the platform."""
    commute = _commute(
        origin_stop_id=_OSTKREUZ_STOP_ID,
        lines=frozenset({"RB32"}),
        weekdays=frozenset({2}),  # Wednesday, matching the fixed `now` below
        window_start=dt.time(17, 0),
        window_end=dt.time(17, 30),
    )
    uid = _seed_commute(tmp_path, commute)

    now = dt.datetime(2026, 9, 30, 15, 0, tzinfo=dt.timezone.utc)  # 17:00 Europe/Berlin
    app.dependency_overrides[get_now] = lambda: now
    payload = json.loads((_RECORDED_FIXTURES_DIR / "platforms_ostkreuz.json").read_text())
    app.dependency_overrides[get_hafas_client] = lambda: _mock_client(payload)
    client.cookies.set("uid", uid)

    response_de = client.get("/today", params={"lang": "de"})
    assert response_de.status_code == 200
    block_de = _departures_block(response_de.text)
    assert '<time datetime="2026-09-30T17:18:00+02:00">17:18</time>' in block_de
    assert '<time datetime="2026-09-30T17:20:00+02:00">17:20</time>' in block_de
    assert "+2 Min." in block_de
    assert "Gleis 14" in block_de

    response_en = client.get("/today", params={"lang": "en"})
    assert response_en.status_code == 200
    block_en = _departures_block(response_en.text)
    assert "+2 min" in block_en
    assert "Platform 14" in block_en


def test_today_platform_change_shown_as_text(client, tmp_path) -> None:
    """Charter G3's platform-change wording ('Gleis 3 statt 1') appears only
    when both the planned and real-time platform are known and differ."""
    commute = _commute()
    uid = _seed_commute(tmp_path, commute)

    payload = {
        "departures": [
            {
                "tripId": "1",
                "stop": {"id": _ORIGIN_STOP_ID, "name": "Origin"},
                "when": "2026-01-05T07:45:00+01:00",
                "plannedWhen": "2026-01-05T07:45:00+01:00",
                "delay": 0,
                "cancelled": False,
                "line": {"id": "line:S41", "name": "S41", "product": "suburban"},
                "plannedPlatform": "1",
                "platform": "3",
                "remarks": [],
            }
        ]
    }

    _override_now(client)
    app.dependency_overrides[get_hafas_client] = lambda: _mock_client(payload)
    client.cookies.set("uid", uid)

    response_de = client.get("/today", params={"lang": "de"})
    assert response_de.status_code == 200
    assert "Gleis 3 statt 1" in _departures_block(response_de.text)

    response_en = client.get("/today", params={"lang": "en"})
    assert response_en.status_code == 200
    assert "Platform 3 instead of 1" in _departures_block(response_en.text)


def test_today_no_departures_in_window_shows_localized_line(client, tmp_path) -> None:
    commute = _commute()
    uid = _seed_commute(tmp_path, commute)

    _override_now(client)
    app.dependency_overrides[get_hafas_client] = lambda: _mock_client({"departures": []})
    client.cookies.set("uid", uid)

    response_de = client.get("/today", params={"lang": "de"})
    assert response_de.status_code == 200
    assert "Keine Abfahrten in diesem Zeitfenster." in response_de.text
    assert 'class="departures"' not in response_de.text

    response_en = client.get("/today", params={"lang": "en"})
    assert response_en.status_code == 200
    assert "No departures in this window." in response_en.text


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


_WESTKREUZ_STOP_ID = "900024102"
_HAUPTBAHNHOF_PARENT_STOP_ID = "900003201"
_ALTERNATIVES_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "hafas" / "alternatives"


class _DeparturesOnlyHafasClient:
    """A HafasClient stand-in that only answers `/stops/.../departures`;
    used to prove a `/journeys` failure never turns into an error page."""

    def __init__(self, departures_payload: dict) -> None:
        self._payload = departures_payload

    def departures(self, stop_id: str, when: dt.datetime, duration: int) -> dict:
        return self._payload

    def journeys(self, from_id: str, to_id: str, departure: dt.datetime):
        raise HafasError("boom")


def _child_stop_alternative_hafas_client() -> HafasClient:
    disrupted = json.loads((_RECORDED_FIXTURES_DIR / "departures_cancellation.json").read_text())
    alternative = json.loads(
        (_ALTERNATIVES_FIXTURES_DIR / "synthetic_child_stop_arrival.json").read_text()
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/stops/{_WESTKREUZ_STOP_ID}/departures":
            return httpx.Response(200, json=disrupted)
        if request.url.path == "/journeys":
            return httpx.Response(200, json=alternative)
        raise AssertionError(f"unexpected HAFAS request: {request.url.path}")

    return HafasClient(httpx.Client(transport=httpx.MockTransport(handler)))


def _disrupted_child_stop_commute(**overrides) -> Commute:
    defaults = dict(
        origin_stop_id=_WESTKREUZ_STOP_ID,  # recorded cancellation fixture (S46 07:20)
        destination_stop_id=_HAUPTBAHNHOF_PARENT_STOP_ID,  # parent of the fixture's arrival stop
        lines=frozenset({"S46"}),
        weekdays=frozenset({2}),  # Wednesday, matching _RECORDED_NOW
        window_start=dt.time(7, 0),
        window_end=dt.time(7, 30),
    )
    defaults.update(overrides)
    return Commute(**defaults)


def test_today_disrupted_commute_shows_suggested_alternative(client, tmp_path) -> None:
    """Charter G3: a disrupted card shows the suggested alternative, proven
    end to end for the case where HAFAS reports the journey's arrival at a
    child stop of the saved destination -- the saved destination here is the
    parent station 900003201 (S+U Berlin Hauptbahnhof), while the only
    qualifying journey in the fixture arrives at its child stop 900003200."""
    commute = _disrupted_child_stop_commute()
    uid = _seed_commute(tmp_path, commute)

    app.dependency_overrides[get_now] = lambda: _RECORDED_NOW
    app.dependency_overrides[get_hafas_client] = _child_stop_alternative_hafas_client
    client.cookies.set("uid", uid)

    response_de = client.get("/today", params={"lang": "de"})
    assert response_de.status_code == 200
    card_de = _card_block(response_de.text, "disrupted")
    assert "Alternative:" in card_de
    assert "U2 07:41 → S+U Berlin Hauptbahnhof [Gleis 1-8] an 07:58" in card_de

    response_en = client.get("/today", params={"lang": "en"})
    assert response_en.status_code == 200
    card_en = _card_block(response_en.text, "disrupted")
    assert "Alternative:" in card_en
    assert "U2 07:41 → S+U Berlin Hauptbahnhof [Gleis 1-8] arr 07:58" in card_en


def test_today_ok_commute_does_not_call_journeys(client, tmp_path) -> None:
    """The alternative lookup is a per-disrupted-commute call (charter G3):
    an unaffected commute must never trigger a `/journeys` request."""
    commute = _commute(
        origin_stop_id=_ALEXANDERPLATZ_STOP_ID,
        destination_stop_id=_OSTKREUZ_STOP_ID,
        lines=frozenset({"S3"}),
        weekdays=frozenset({2}),
        window_start=dt.time(7, 0),
        window_end=dt.time(7, 30),
    )
    uid = _seed_commute(tmp_path, commute)
    undisturbed = json.loads((_RECORDED_FIXTURES_DIR / "departures_undisturbed.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/stops/{_ALEXANDERPLATZ_STOP_ID}/departures":
            return httpx.Response(200, json=undisturbed)
        raise AssertionError(f"unexpected HAFAS request: {request.url.path}")

    app.dependency_overrides[get_now] = lambda: _RECORDED_NOW
    app.dependency_overrides[get_hafas_client] = lambda: HafasClient(
        httpx.Client(transport=httpx.MockTransport(handler))
    )
    client.cookies.set("uid", uid)

    response = client.get("/today")

    assert response.status_code == 200
    assert 'data-status="ok"' in response.text


def test_today_disrupted_commute_with_journeys_error_stays_disrupted_without_error_page(
    client, tmp_path
) -> None:
    """Notes from earlier attempts (T-0014): a HafasError from `/journeys`
    leaves the card disrupted, with no alternative shown and no error page."""
    commute = _disrupted_child_stop_commute()
    uid = _seed_commute(tmp_path, commute)
    disrupted = json.loads((_RECORDED_FIXTURES_DIR / "departures_cancellation.json").read_text())

    app.dependency_overrides[get_now] = lambda: _RECORDED_NOW
    app.dependency_overrides[get_hafas_client] = lambda: _DeparturesOnlyHafasClient(disrupted)
    client.cookies.set("uid", uid)

    response = client.get("/today")

    assert response.status_code == 200
    assert "Traceback" not in response.text
    card = _card_block(response.text, "disrupted")
    assert "Alternative:" not in card


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


def _recorded_multi_status_hafas_client() -> HafasClient:
    """Routes each origin stop id to its recorded fixture (charter G3's
    "one page with an OK, a disrupted, a paused and a failed commute");
    raises if the paused commute's origin is ever requested."""
    undisturbed = json.loads((_RECORDED_FIXTURES_DIR / "departures_undisturbed.json").read_text())
    warning = json.loads((_RECORDED_FIXTURES_DIR / "departures_warning.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/stops/{_ALEXANDERPLATZ_STOP_ID}/departures":
            return httpx.Response(200, json=undisturbed)
        if request.url.path == f"/stops/{_OSTKREUZ_STOP_ID}/departures":
            return httpx.Response(200, json=warning)
        if request.url.path == f"/stops/{_FAILED_STOP_ID}/departures":
            return httpx.Response(404)
        if request.url.path == f"/stops/{_PAUSED_STOP_ID}/departures":
            raise AssertionError("a paused commute's origin must never be fetched")
        if request.url.path == "/journeys":
            # The disrupted commute (Ostkreuz -> Hauptbahnhof) triggers one
            # alternative lookup; no qualifying journey here, this test only
            # cares about the per-status card rendering.
            return httpx.Response(200, json={"journeys": []})
        raise AssertionError(f"unexpected HAFAS request: {request.url.path}")

    return HafasClient(httpx.Client(transport=httpx.MockTransport(handler)))


def test_today_shows_one_card_per_status_with_stop_names_and_no_raw_ids(client, tmp_path) -> None:
    # Weekday 2 (Wednesday) matches _RECORDED_NOW's date; window times
    # bracket the recorded fixtures' plannedWhen (undisturbed: 07:xx,
    # warning's cancelled RB26: 08:06).
    ok_commute = Commute(
        origin_stop_id=_ALEXANDERPLATZ_STOP_ID,
        destination_stop_id=_OSTKREUZ_STOP_ID,
        origin_name="S+U Alexanderplatz Bhf (Berlin)",
        destination_name="S Ostkreuz Bhf (Berlin)",
        lines=frozenset({"S3"}),
        weekdays=frozenset({2}),
        window_start=dt.time(7, 0),
        window_end=dt.time(7, 30),
    )
    disrupted_commute = Commute(
        origin_stop_id=_OSTKREUZ_STOP_ID,
        destination_stop_id=_ALEXANDERPLATZ_STOP_ID,
        origin_name="S Ostkreuz Bhf (Berlin)",
        destination_name="S+U Hauptbahnhof (Berlin)",
        lines=frozenset({"RB26"}),
        weekdays=frozenset({2}),
        window_start=dt.time(8, 0),
        window_end=dt.time(8, 30),
    )
    paused_commute = Commute(
        origin_stop_id=_PAUSED_STOP_ID,
        destination_stop_id=_ALEXANDERPLATZ_STOP_ID,
        origin_name="S Görlitzer Bahnhof (Berlin)",
        destination_name="S+U Alexanderplatz Bhf (Berlin)",
        lines=frozenset({"U1"}),
        weekdays=frozenset({2}),
        window_start=dt.time(7, 0),
        window_end=dt.time(7, 30),
        paused=True,
    )
    failed_commute = Commute(
        origin_stop_id=_FAILED_STOP_ID,
        destination_stop_id=_ALEXANDERPLATZ_STOP_ID,
        origin_name="S Warschauer Str. (Berlin)",
        destination_name="S+U Alexanderplatz Bhf (Berlin)",
        lines=frozenset({"S3"}),
        weekdays=frozenset({2}),
        window_start=dt.time(7, 0),
        window_end=dt.time(7, 30),
    )
    uid = _seed_commutes(
        tmp_path, [ok_commute, disrupted_commute, paused_commute, failed_commute]
    )

    app.dependency_overrides[get_now] = lambda: _RECORDED_NOW
    app.dependency_overrides[get_hafas_client] = _recorded_multi_status_hafas_client
    client.cookies.set("uid", uid)

    response = client.get("/today", params={"lang": "de"})
    assert response.status_code == 200
    text = response.text

    assert "S+U Alexanderplatz Bhf (Berlin) → S Ostkreuz Bhf (Berlin)" in text
    assert "S Ostkreuz Bhf (Berlin) → S+U Hauptbahnhof (Berlin)" in text
    assert "S Görlitzer Bahnhof (Berlin) → S+U Alexanderplatz Bhf (Berlin)" in text
    assert "S Warschauer Str. (Berlin) → S+U Alexanderplatz Bhf (Berlin)" in text

    assert "OK" in text
    assert "Gestört" in text
    assert "Pausiert" in text
    assert "Prüfung fehlgeschlagen" in text

    assert 'data-status="ok"' in text
    assert 'data-status="disrupted"' in text
    assert 'data-status="paused"' in text
    assert 'data-status="failed"' in text

    for stop_id in (
        _ALEXANDERPLATZ_STOP_ID,
        _OSTKREUZ_STOP_ID,
        _PAUSED_STOP_ID,
        _FAILED_STOP_ID,
    ):
        assert stop_id not in text

    # Charter G3: paused and failed cards show no departures list at all,
    # not even the "no departures in this window" line -- a regression that
    # started passing departures=[] for them would otherwise go unnoticed.
    for status in ("paused", "failed"):
        block = _card_block(text, status)
        assert 'class="departures"' not in block
        assert "Keine Abfahrten in diesem Zeitfenster." not in block


def test_today_card_with_empty_stored_names_shows_generic_label(client, tmp_path) -> None:
    """A commute saved before origin/destination names were stored (charter
    G3) has empty origin_name/destination_name; the card must still show a
    localized label, never a blank arrow or a stop id."""
    commute = Commute(
        origin_stop_id=_ORIGIN_STOP_ID,
        destination_stop_id=_DESTINATION_STOP_ID,
        lines=frozenset({"S41"}),
        weekdays=frozenset({5}),  # Saturday only, inactive on _NOW's Monday
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
    )
    uid = _seed_commute(tmp_path, commute)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("HAFAS must not be called for a commute inactive today")

    _override_now(client)
    app.dependency_overrides[get_hafas_client] = lambda: HafasClient(
        httpx.Client(transport=httpx.MockTransport(handler))
    )
    client.cookies.set("uid", uid)

    response_de = client.get("/today", params={"lang": "de"})
    assert response_de.status_code == 200
    assert "Gespeicherte Verbindung" in response_de.text
    assert _ORIGIN_STOP_ID not in response_de.text
    assert _DESTINATION_STOP_ID not in response_de.text

    response_en = client.get("/today", params={"lang": "en"})
    assert response_en.status_code == 200
    assert "Saved commute" in response_en.text


def test_today_g3_definition_of_done(client, tmp_path) -> None:
    """Charter G3 definition of done, all in one render: an OK, a disrupted,
    a paused and a failed-check commute each show their stop names (never
    raw ids), their status label, and -- for OK/disrupted -- planned vs
    real-time departure times; the disrupted card's generic remark summary
    ("Störung.") falls back to cleaned remark text instead of that label.

    Unlike the other tests in this file, `now` here is 07:00 Europe/Berlin
    (not the file's `_RECORDED_NOW`, 08:00) so that the OK commute's window
    (07:15-07:25) and the disrupted commute's window (07:40-07:50) both
    still lie ahead of `now`: `next_departures` only shows departures at or
    after `now`, so at 08:00 U8's 07:18/07:19 departure would already have
    scrolled off the OK card, defeating the "planned vs real-time" check.
    """
    now = dt.datetime(2026, 9, 30, 5, 0, tzinfo=dt.timezone.utc)  # 07:00 Europe/Berlin

    ok_commute = Commute(
        origin_stop_id=_ALEXANDERPLATZ_STOP_ID,
        destination_stop_id=_OSTKREUZ_STOP_ID,
        origin_name="S+U Alexanderplatz Bhf (Berlin)",
        destination_name="S Ostkreuz Bhf (Berlin)",
        lines=frozenset({"U8"}),
        weekdays=frozenset({2}),
        window_start=dt.time(7, 15),
        window_end=dt.time(7, 25),
    )
    disrupted_commute = Commute(
        origin_stop_id=_OSTKREUZ_STOP_ID,
        destination_stop_id=_ALEXANDERPLATZ_STOP_ID,
        origin_name="S Ostkreuz Bhf (Berlin)",
        destination_name="S+U Hauptbahnhof (Berlin)",
        lines=frozenset({"RB32"}),
        weekdays=frozenset({2}),
        window_start=dt.time(7, 40),
        window_end=dt.time(7, 50),
    )
    paused_commute = Commute(
        origin_stop_id=_PAUSED_STOP_ID,
        destination_stop_id=_ALEXANDERPLATZ_STOP_ID,
        origin_name="S Görlitzer Bahnhof (Berlin)",
        destination_name="S+U Alexanderplatz Bhf (Berlin)",
        lines=frozenset({"U1"}),
        weekdays=frozenset({2}),
        window_start=dt.time(7, 0),
        window_end=dt.time(7, 30),
        paused=True,
    )
    failed_commute = Commute(
        origin_stop_id=_FAILED_STOP_ID,
        destination_stop_id=_ALEXANDERPLATZ_STOP_ID,
        origin_name="S Warschauer Str. (Berlin)",
        destination_name="S+U Alexanderplatz Bhf (Berlin)",
        lines=frozenset({"S3"}),
        weekdays=frozenset({2}),
        window_start=dt.time(7, 0),
        window_end=dt.time(7, 30),
    )
    uid = _seed_commutes(
        tmp_path, [ok_commute, disrupted_commute, paused_commute, failed_commute]
    )

    app.dependency_overrides[get_now] = lambda: now
    app.dependency_overrides[get_hafas_client] = _recorded_multi_status_hafas_client
    client.cookies.set("uid", uid)

    response = client.get("/today", params={"lang": "de"})
    assert response.status_code == 200
    text = response.text

    assert "S+U Alexanderplatz Bhf (Berlin) → S Ostkreuz Bhf (Berlin)" in text
    assert "S Ostkreuz Bhf (Berlin) → S+U Hauptbahnhof (Berlin)" in text
    assert "S Görlitzer Bahnhof (Berlin) → S+U Alexanderplatz Bhf (Berlin)" in text
    assert "S Warschauer Str. (Berlin) → S+U Alexanderplatz Bhf (Berlin)" in text

    assert "OK" in text
    assert "Gestört" in text
    assert "Pausiert" in text
    assert "Prüfung fehlgeschlagen" in text

    assert re.search(r"(?<!\d)9\d{8}(?!\d)", text) is None

    cards = text.split('<li data-commute-id=')
    ok_card = next(c for c in cards if 'data-status="ok"' in c)
    disrupted_card = next(c for c in cards if 'data-status="disrupted"' in c)

    assert '<time datetime="2026-09-30T07:18:00+02:00">07:18</time>' in ok_card
    assert '<time datetime="2026-09-30T07:19:00+02:00">07:19</time>' in ok_card

    assert "Ausfall" in disrupted_card
    assert "Oranienburg" in disrupted_card
    assert "<a href" not in disrupted_card
    assert "Störung auf RB32: Störung." not in disrupted_card


def test_home_links_to_today() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/today"' in response.text

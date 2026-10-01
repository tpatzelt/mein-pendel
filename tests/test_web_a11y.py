"""HTTP-level tests for accessibility (charter G2): error messages announced
to screen readers via role="alert" and an accessible name on the footer nav.

Offline; failure/invalid-input paths mirror tests/test_web_stops.py,
tests/test_web_commutes.py and tests/test_web_notifications.py.
"""

from __future__ import annotations

import datetime as dt
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from pendel import db
from pendel.app import app, get_hafas_client, get_now
from pendel.hafas import HafasClient, HafasError

_UNDISTURBED = json.loads(
    (Path(__file__).parent / "fixtures" / "hafas" / "recorded" / "departures_undisturbed.json").read_text()
)
_LOCATIONS_ALEXANDERPLATZ = json.loads(
    (Path(__file__).parent / "fixtures" / "hafas" / "recorded" / "locations_alexanderplatz.json").read_text()
)

_ORIGIN_STOP_ID = "900100003"
_ORIGIN_NAME = "S+U Alexanderplatz Bhf (Berlin)"
_DESTINATION_STOP_ID = "900120003"
_DESTINATION_NAME = "S Ostkreuz Bhf (Berlin)"
_NOW = dt.datetime(2026, 9, 30, 6, 50, tzinfo=dt.timezone.utc)
_SKIP_INPUT_TYPES = {"hidden", "submit", "button"}


class _FailingHafasClient:
    def locations(self, query: str) -> list[dict[str, str]]:
        raise HafasError("boom")


def _undisturbed_hafas_client() -> HafasClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_UNDISTURBED)

    return HafasClient(httpx.Client(transport=httpx.MockTransport(handler)))


def _locations_hafas_client() -> HafasClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_LOCATIONS_ALEXANDERPLATZ)

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
        "window_start": "25:00",
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


# T-0055: every visible form control on the setup (/stops, /commutes/new),
# edit and /notifications pages has an associated label, in German and
# English (charter G4). Visible: input other than type hidden/submit/button,
# select or textarea. Labelled: its id is referenced by a <label for=...>
# on the page, or it sits inside an open <label>. Parsed with the stdlib
# html.parser so no dependency is added.


class _LabelInventory(HTMLParser):
    """Visible form controls, <label for=...> targets, and which fieldsets
    wrap a checkbox and have a non-empty legend."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.label_for_targets: set[str] = set()
        self.controls: list[dict[str, Any]] = []
        self.fieldsets: list[dict[str, Any]] = []
        self._label_depth = 0
        self._fieldset_stack: list[dict[str, Any]] = []
        self._in_legend = False
        self._legend_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = dict(attrs)
        if tag == "label":
            self._label_depth += 1
            for_target = attrs_d.get("for")
            if for_target:
                self.label_for_targets.add(for_target)
        elif tag == "fieldset":
            self._fieldset_stack.append({"legend": "", "has_checkbox": False})
        elif tag == "legend":
            self._in_legend = True
            self._legend_text = []
        elif tag in ("input", "select", "textarea"):
            input_type = (attrs_d.get("type") or "text").lower() if tag == "input" else None
            if tag == "input" and input_type in _SKIP_INPUT_TYPES:
                return
            control = {
                "tag": tag,
                "type": input_type,
                "id": attrs_d.get("id"),
                "name": attrs_d.get("name"),
                "wrapped_in_label": self._label_depth > 0,
            }
            self.controls.append(control)
            if tag == "input" and input_type == "checkbox" and self._fieldset_stack:
                self._fieldset_stack[-1]["has_checkbox"] = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "label":
            self._label_depth = max(0, self._label_depth - 1)
        elif tag == "legend":
            self._in_legend = False
            if self._fieldset_stack:
                self._fieldset_stack[-1]["legend"] = "".join(self._legend_text).strip()
        elif tag == "fieldset":
            if self._fieldset_stack:
                self.fieldsets.append(self._fieldset_stack.pop())

    def handle_data(self, data: str) -> None:
        if self._in_legend:
            self._legend_text.append(data)

    def unlabelled_controls(self) -> list[dict[str, Any]]:
        return [
            control
            for control in self.controls
            if not (
                control["wrapped_in_label"]
                or (control["id"] and control["id"] in self.label_for_targets)
            )
        ]


def _parse_labels(html_text: str) -> _LabelInventory:
    inventory = _LabelInventory()
    inventory.feed(html_text)
    return inventory


def _assert_every_visible_control_is_labelled(inventory: _LabelInventory, page: str) -> None:
    # Guards against the test passing vacuously because the page rendered
    # no controls at all (e.g. a route that returned the wrong template).
    assert inventory.controls, f"{page}: expected at least one visible form control to check"
    unlabelled = inventory.unlabelled_controls()
    assert not unlabelled, f"{page}: unlabelled control(s): {unlabelled}"


def _assert_checkbox_groups_are_in_legended_fieldsets(inventory: _LabelInventory, page: str) -> None:
    checkbox_fieldsets = [fieldset for fieldset in inventory.fieldsets if fieldset["has_checkbox"]]
    assert checkbox_fieldsets, f"{page}: expected a <fieldset> wrapping a checkbox group"
    for fieldset in checkbox_fieldsets:
        assert fieldset["legend"], f"{page}: checkbox fieldset has no non-empty <legend>"


def test_stops_page_controls_are_labelled_de_and_en() -> None:
    client = TestClient(app)

    for lang in ("de", "en"):
        response = client.get("/stops", params={"lang": lang})
        assert response.status_code == 200
        _assert_every_visible_control_is_labelled(_parse_labels(response.text), f"/stops ({lang})")


def test_stops_page_with_results_controls_are_labelled_de_and_en() -> None:
    client = TestClient(app)
    app.dependency_overrides[get_hafas_client] = _locations_hafas_client

    for lang in ("de", "en"):
        response = client.get("/stops", params={"q": "Alexanderplatz", "lang": lang})
        assert response.status_code == 200
        _assert_every_visible_control_is_labelled(
            _parse_labels(response.text), f"/stops?q=Alexanderplatz ({lang})"
        )


def test_commutes_new_form_controls_are_labelled_de_and_en() -> None:
    client = TestClient(app)
    app.dependency_overrides[get_hafas_client] = _undisturbed_hafas_client
    app.dependency_overrides[get_now] = lambda: _NOW
    params = {
        "origin_stop_id": _ORIGIN_STOP_ID,
        "origin_name": _ORIGIN_NAME,
        "destination_stop_id": _DESTINATION_STOP_ID,
        "destination_name": _DESTINATION_NAME,
    }

    for lang in ("de", "en"):
        response = client.get("/commutes/new", params=params | {"lang": lang})
        assert response.status_code == 200
        inventory = _parse_labels(response.text)
        _assert_every_visible_control_is_labelled(inventory, f"/commutes/new ({lang})")
        _assert_checkbox_groups_are_in_legended_fieldsets(inventory, f"/commutes/new ({lang})")


def test_commute_edit_form_controls_are_labelled_de_and_en(tmp_path) -> None:
    client = TestClient(app)
    app.dependency_overrides[get_hafas_client] = _undisturbed_hafas_client
    app.dependency_overrides[get_now] = lambda: _NOW
    form = {
        "origin_stop_id": _ORIGIN_STOP_ID,
        "origin_name": _ORIGIN_NAME,
        "destination_stop_id": _DESTINATION_STOP_ID,
        "destination_name": _DESTINATION_NAME,
        "lines": "S3",
        "weekdays": "0",
        "window_start": "07:30",
        "window_end": "08:00",
        "delay_threshold_min": "5",
    }

    create_response = client.post("/commutes", data=form, follow_redirects=False)
    assert create_response.status_code == 303
    uid = create_response.cookies.get("uid")
    client.cookies.set("uid", uid)
    conn = db.connect(tmp_path / "pendel.db")
    try:
        commute_id, _ = db.list_commutes(conn, uid)[0]
    finally:
        conn.close()

    for lang in ("de", "en"):
        response = client.get(f"/commutes/{commute_id}/edit", params={"lang": lang})
        assert response.status_code == 200
        inventory = _parse_labels(response.text)
        _assert_every_visible_control_is_labelled(inventory, f"/commutes/{{id}}/edit ({lang})")
        _assert_checkbox_groups_are_in_legended_fieldsets(inventory, f"/commutes/{{id}}/edit ({lang})")


def test_notifications_page_controls_are_labelled_de_and_en(tmp_path) -> None:
    client = TestClient(app)
    uid = _seed_user(tmp_path)
    client.cookies.set("uid", uid)

    for lang in ("de", "en"):
        response = client.get("/notifications", params={"lang": lang})
        assert response.status_code == 200
        _assert_every_visible_control_is_labelled(_parse_labels(response.text), f"/notifications ({lang})")

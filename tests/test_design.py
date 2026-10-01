"""Design-system checks for the stylesheet (charter G4).

Covers custom-property tokens defined in :root, a dark-mode block that
redefines every color token, the absence of raw hex/rgb color literals
outside those two blocks, and 44px minimum touch targets on links and
buttons. Plain regex parsing, no CSS parser dependency.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import struct
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from pendel import db
from pendel.app import app, get_hafas_client, get_now
from pendel.commute import Commute
from pendel.hafas import HafasClient

_CSS_PATH = Path(__file__).parent.parent / "src" / "pendel" / "static" / "css" / "style.css"
_CSS = _CSS_PATH.read_text(encoding="utf-8")

_STATIC_DIR = Path(__file__).parent.parent / "src" / "pendel" / "static"
_MANIFEST_PATH = _STATIC_DIR / "manifest.json"

_MANIFEST_PAGES = (
    "/",
    "/stops",
    "/today",
    "/notifications",
    "/about",
    "/impressum",
    "/datenschutz",
    "/commutes",
)


def _empty_hafas_client() -> HafasClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    return HafasClient(httpx.Client(transport=httpx.MockTransport(handler)))


def _no_departures_hafas_client() -> HafasClient:
    """/commutes/new and the edit page build their line checkboxes from
    `{"departures": [...]}` (see pendel.engine.line_choices), unlike
    `_empty_hafas_client`'s bare `[]` used by the other manifest pages."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"departures": []})

    return HafasClient(httpx.Client(transport=httpx.MockTransport(handler)))


def _seed_commute(tmp_path: Path) -> tuple[str, int]:
    conn = db.connect(tmp_path / "pendel.db")
    try:
        uid = db.create_user(conn)
        commute_id = db.add_commute(
            conn,
            uid,
            Commute(
                origin_stop_id="900100003",
                destination_stop_id="900120003",
                lines=frozenset({"S3"}),
                weekdays=frozenset({0, 1, 2, 3, 4}),
                window_start=dt.time(7, 30),
                window_end=dt.time(8, 0),
                origin_name="A",
                destination_name="B",
            ),
        )
        return uid, commute_id
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDEL_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def client():
    test_client = TestClient(app)
    app.dependency_overrides[get_hafas_client] = _empty_hafas_client
    app.dependency_overrides[get_now] = lambda: dt.datetime(2026, 9, 30, 6, 0, tzinfo=dt.timezone.utc)
    yield test_client
    test_client.cookies.clear()
    app.dependency_overrides.clear()


_ROOT_BLOCK_RE = re.compile(r":root\s*\{(?P<body>[^}]*)\}", re.DOTALL)
_DARK_BLOCK_RE = re.compile(
    r"@media\s*\(\s*prefers-color-scheme:\s*dark\s*\)\s*\{.*?:root\s*\{(?P<body>[^}]*)\}.*?\}",
    re.DOTALL,
)
_CUSTOM_PROP_RE = re.compile(r"(--[a-zA-Z0-9-]+)\s*:")
_CUSTOM_PROP_VALUE_RE = re.compile(r"(--[a-zA-Z0-9-]+)\s*:\s*([^;]+);")
_COLOR_LITERAL_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(")
_MIN_HEIGHT_RE = re.compile(r"min-height\s*:\s*(\d+)px")

_STATUSES = ("ok", "disrupted", "paused", "failed")


def _tokens(block: str) -> dict[str, str]:
    return {name: value.strip() for name, value in _CUSTOM_PROP_VALUE_RE.findall(block)}


def _srgb_to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    assert len(hex_color) == 6, f"expected a 6-digit hex color, got {hex_color!r}"
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _srgb_to_linear(r) + 0.7152 * _srgb_to_linear(g) + 0.0722 * _srgb_to_linear(b)


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    lum_a, lum_b = _relative_luminance(hex_a), _relative_luminance(hex_b)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def _root_block() -> str:
    match = _ROOT_BLOCK_RE.search(_CSS)
    assert match, "expected a :root block declaring design tokens"
    return match.group("body")


def _dark_block() -> str:
    match = _DARK_BLOCK_RE.search(_CSS)
    assert match, "expected an @media (prefers-color-scheme: dark) block with a :root override"
    return match.group("body")


def test_root_declares_color_space_and_font_tokens() -> None:
    root = _root_block()
    props = set(_CUSTOM_PROP_RE.findall(root))

    assert any(p.startswith("--color-") for p in props), "missing --color-* tokens in :root"
    assert any(p.startswith("--space-") for p in props), "missing --space-* tokens in :root"
    assert any(p.startswith("--font-") for p in props), "missing --font-* tokens in :root"


def test_dark_mode_redefines_every_color_token() -> None:
    root_colors = {p for p in _CUSTOM_PROP_RE.findall(_root_block()) if p.startswith("--color-")}
    dark_colors = {p for p in _CUSTOM_PROP_RE.findall(_dark_block()) if p.startswith("--color-")}

    assert root_colors, "no --color-* tokens found in :root"
    missing = root_colors - dark_colors
    assert not missing, f"dark-mode block does not redefine: {sorted(missing)}"


def test_no_raw_color_literals_outside_root_and_dark_blocks() -> None:
    css_without_tokens = _CSS
    for block_re in (_ROOT_BLOCK_RE, _DARK_BLOCK_RE):
        css_without_tokens = block_re.sub("", css_without_tokens)

    matches = _COLOR_LITERAL_RE.findall(css_without_tokens)
    assert not matches, f"raw color literal(s) found outside :root/dark blocks: {matches}"


def test_links_and_buttons_meet_minimum_touch_target() -> None:
    rule_re = re.compile(r"([^{}]+)\{([^}]*)\}")
    covers_a = False
    covers_button = False

    for selector, body in rule_re.findall(_CSS):
        heights = [int(h) for h in _MIN_HEIGHT_RE.findall(body)]
        if not heights or min(heights) < 44:
            continue
        selectors = [s.strip() for s in selector.split(",")]
        if any(s == "a" or s.endswith(" a") for s in selectors):
            covers_a = True
        if any(s == "button" for s in selectors):
            covers_button = True

    assert covers_a, "expected a rule covering `a` with min-height >= 44px"
    assert covers_button, "expected a rule covering `button` with min-height >= 44px"


def test_status_rules_reference_matching_custom_properties() -> None:
    rule_re = re.compile(r"(\.status-[a-z]+)\s*\{([^}]*)\}")
    rules = {selector: body for selector, body in rule_re.findall(_CSS)}

    for status in _STATUSES:
        selector = f".status-{status}"
        assert selector in rules, f"missing {selector} rule"
        body = rules[selector]
        assert f"var(--status-{status}-fg)" in body, f"{selector} does not use --status-{status}-fg"
        assert f"var(--status-{status}-bg)" in body, f"{selector} does not use --status-{status}-bg"


def test_status_colors_meet_contrast_in_light_and_dark_mode() -> None:
    for block, label in ((_root_block(), "light"), (_dark_block(), "dark")):
        tokens = _tokens(block)
        for status in _STATUSES:
            fg, bg = tokens.get(f"--status-{status}-fg"), tokens.get(f"--status-{status}-bg")
            assert fg and bg, f"missing --status-{status}-fg/bg in {label} mode"
            ratio = _contrast_ratio(fg, bg)
            assert ratio >= 4.5, f"{label} mode --status-{status}: contrast {ratio:.2f} < 4.5"


def test_text_and_error_meet_contrast_on_background_in_light_and_dark_mode() -> None:
    for block, label in ((_root_block(), "light"), (_dark_block(), "dark")):
        tokens = _tokens(block)
        bg = tokens["--color-bg"]
        for name in ("--color-text", "--color-error"):
            ratio = _contrast_ratio(tokens[name], bg)
            assert ratio >= 4.5, f"{label} mode {name} on --color-bg: contrast {ratio:.2f} < 4.5"


@pytest.mark.parametrize("path", _MANIFEST_PAGES)
def test_page_links_manifest(client, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert 'rel="manifest"' in response.text
    assert '<meta name="viewport"' in response.text


def test_commutes_new_page_links_manifest(client) -> None:
    app.dependency_overrides[get_hafas_client] = _no_departures_hafas_client

    response = client.get(
        "/commutes/new",
        params={
            "origin_stop_id": "900100003",
            "origin_name": "A",
            "destination_stop_id": "900120003",
            "destination_name": "B",
        },
    )

    assert 'rel="manifest"' in response.text
    assert '<meta name="viewport"' in response.text


def test_commute_edit_page_links_manifest(client, tmp_path) -> None:
    app.dependency_overrides[get_hafas_client] = _no_departures_hafas_client
    uid, commute_id = _seed_commute(tmp_path)
    client.cookies.set("uid", uid)

    response = client.get(f"/commutes/{commute_id}/edit")

    assert 'rel="manifest"' in response.text
    assert '<meta name="viewport"' in response.text


def test_manifest_served_with_valid_json_and_required_fields() -> None:
    client = TestClient(app)

    response = client.get("/static/manifest.json")

    assert response.status_code == 200
    manifest = response.json()
    assert manifest["name"] == "Mein Pendel"
    assert manifest["short_name"] == "Pendel"
    assert manifest["start_url"] == "/today"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert "background_color" in manifest
    assert "theme_color" in manifest
    assert manifest["icons"]


def test_manifest_icons_exist_and_png_dimensions_match_declared_sizes() -> None:
    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["icons"], "manifest declares no icons"
    for icon in manifest["icons"]:
        icon_path = _STATIC_DIR / icon["src"]
        assert icon_path.is_file(), f"missing icon file: {icon_path}"

        header = icon_path.read_bytes()[:24]
        assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{icon_path} is not a PNG"
        width, height = struct.unpack(">II", header[16:24])

        declared_width, declared_height = (int(n) for n in icon["sizes"].split("x"))
        assert (width, height) == (declared_width, declared_height), (
            f"{icon_path} is {width}x{height}, manifest declares {icon['sizes']}"
        )

"""Design-system checks for the stylesheet (charter G4).

Covers custom-property tokens defined in :root, a dark-mode block that
redefines every color token, the absence of raw hex/rgb color literals
outside those two blocks, and 44px minimum touch targets on links and
buttons. Plain regex parsing, no CSS parser dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

_CSS_PATH = Path(__file__).parent.parent / "src" / "pendel" / "static" / "css" / "style.css"
_CSS = _CSS_PATH.read_text(encoding="utf-8")

_ROOT_BLOCK_RE = re.compile(r":root\s*\{(?P<body>[^}]*)\}", re.DOTALL)
_DARK_BLOCK_RE = re.compile(
    r"@media\s*\(\s*prefers-color-scheme:\s*dark\s*\)\s*\{.*?:root\s*\{(?P<body>[^}]*)\}.*?\}",
    re.DOTALL,
)
_CUSTOM_PROP_RE = re.compile(r"(--[a-zA-Z0-9-]+)\s*:")
_COLOR_LITERAL_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(")
_MIN_HEIGHT_RE = re.compile(r"min-height\s*:\s*(\d+)px")


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

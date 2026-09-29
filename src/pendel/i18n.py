"""Minimal DE/EN string table and language resolution (charter G2: German and English UI).

Language is resolved once per request via `resolve_language`, in priority order:
1. `?lang=de` or `?lang=en` query parameter,
2. a previously-set `lang` cookie,
3. the `Accept-Language` header (first supported tag wins),
4. `de`, the charter's default.
"""

from __future__ import annotations

SUPPORTED_LANGUAGES = ("de", "en")
DEFAULT_LANGUAGE = "de"

STRINGS: dict[str, dict[str, str]] = {
    "de": {
        "app_title": "Mein Pendel",
        "home_heading": "Mein Pendel",
        "home_intro": "Behalte deine Verbindung im Blick.",
        "footer_text": "Mein Pendel",
        "stops_heading": "Haltestelle suchen",
        "stops_search_label": "Haltestelle oder Adresse",
        "stops_placeholder": "z. B. Alexanderplatz",
        "stops_submit": "Suchen",
        "stops_no_results": "Keine Treffer.",
        "stops_error": "Haltestellensuche ist gerade nicht verfügbar. Bitte später erneut versuchen.",
    },
    "en": {
        "app_title": "Mein Pendel",
        "home_heading": "Mein Pendel",
        "home_intro": "Keep an eye on your commute.",
        "footer_text": "Mein Pendel",
        "stops_heading": "Search for a stop",
        "stops_search_label": "Stop or address",
        "stops_placeholder": "e.g. Alexanderplatz",
        "stops_submit": "Search",
        "stops_no_results": "No results.",
        "stops_error": "Stop search is temporarily unavailable. Please try again later.",
    },
}


def resolve_language(
    query_lang: str | None,
    cookie_lang: str | None,
    accept_language: str | None,
) -> str:
    """Resolve the UI language from query param, cookie, then Accept-Language."""
    if query_lang in SUPPORTED_LANGUAGES:
        return query_lang
    if cookie_lang in SUPPORTED_LANGUAGES:
        return cookie_lang
    if accept_language:
        for tag in accept_language.split(","):
            code = tag.split(";")[0].strip().lower()[:2]
            if code in SUPPORTED_LANGUAGES:
                return code
    return DEFAULT_LANGUAGE


def translate(language: str, key: str) -> str:
    """Look up `key` in `language`'s string table, falling back to the default language."""
    table = STRINGS.get(language, STRINGS[DEFAULT_LANGUAGE])
    return table.get(key, STRINGS[DEFAULT_LANGUAGE][key])

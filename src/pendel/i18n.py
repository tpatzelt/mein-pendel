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
        "commute_new_heading": "Verbindung speichern",
        "commute_new_origin_label": "Start-Haltestelle (ID)",
        "commute_new_destination_label": "Ziel-Haltestelle (ID)",
        "commute_new_lines_label": "Linien",
        "commute_new_lines_placeholder": "z. B. S41, M4",
        "commute_new_weekdays_label": "Wochentage",
        "weekday_mon": "Mo",
        "weekday_tue": "Di",
        "weekday_wed": "Mi",
        "weekday_thu": "Do",
        "weekday_fri": "Fr",
        "weekday_sat": "Sa",
        "weekday_sun": "So",
        "commute_new_window_start_label": "Abfahrtsfenster von",
        "commute_new_window_end_label": "Abfahrtsfenster bis",
        "commute_new_delay_label": "Verspätung ab (Minuten)",
        "commute_new_submit": "Speichern",
        "commute_new_error": "Bitte alle Felder korrekt ausfüllen: Haltestellen, mindestens eine Linie, mindestens ein Wochentag und gültige Uhrzeiten.",
        "delete_me_button": "Meine Daten löschen",
        "delete_me_done": "Deine Daten wurden gelöscht.",
        "today_heading": "Heute auf meiner Strecke",
        "today_empty": "Du hast noch keine Verbindung gespeichert.",
        "today_empty_cta": "Haltestelle suchen",
        "today_inactive": "Heute nicht aktiv.",
        "today_unavailable": "Status gerade nicht verfügbar.",
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
        "commute_new_heading": "Save a commute",
        "commute_new_origin_label": "Origin stop (ID)",
        "commute_new_destination_label": "Destination stop (ID)",
        "commute_new_lines_label": "Lines",
        "commute_new_lines_placeholder": "e.g. S41, M4",
        "commute_new_weekdays_label": "Weekdays",
        "weekday_mon": "Mon",
        "weekday_tue": "Tue",
        "weekday_wed": "Wed",
        "weekday_thu": "Thu",
        "weekday_fri": "Fri",
        "weekday_sat": "Sat",
        "weekday_sun": "Sun",
        "commute_new_window_start_label": "Departure window from",
        "commute_new_window_end_label": "Departure window to",
        "commute_new_delay_label": "Delay threshold (minutes)",
        "commute_new_submit": "Save",
        "commute_new_error": "Please fill in all fields correctly: stops, at least one line, at least one weekday and valid times.",
        "delete_me_button": "Delete my data",
        "delete_me_done": "Your data has been deleted.",
        "today_heading": "Today on my route",
        "today_empty": "You have not saved a commute yet.",
        "today_empty_cta": "Search for a stop",
        "today_inactive": "Not active today.",
        "today_unavailable": "Status is temporarily unavailable.",
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

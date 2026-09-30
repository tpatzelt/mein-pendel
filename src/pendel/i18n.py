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
        "stops_origin_label": "Start",
        "stops_destination_hint": "Wähle jetzt die Ziel-Haltestelle.",
        "commute_new_heading": "Verbindung speichern",
        "commute_new_missing_stops": "Bitte zuerst Start und Ziel auswählen.",
        "commute_new_origin_label": "Start",
        "commute_new_destination_label": "Ziel",
        "commute_new_lines_label": "Linien",
        "commute_new_lines_error": "Linien sind gerade nicht verfügbar. Bitte später erneut versuchen.",
        "commute_new_retry_cta": "Erneut versuchen",
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
        "commutes_heading": "Meine Verbindungen",
        "delete_me_button": "Meine Daten löschen",
        "delete_me_done": "Deine Daten wurden gelöscht.",
        "today_heading": "Heute auf meiner Strecke",
        "today_empty": "Du hast noch keine Verbindung gespeichert.",
        "today_empty_cta": "Haltestelle suchen",
        "today_inactive": "Heute nicht aktiv.",
        "today_unavailable": "Status gerade nicht verfügbar.",
        "today_status_ok": "OK",
        "today_status_disrupted": "Gestört",
        "today_status_paused": "Pausiert",
        "today_status_failed": "Prüfung fehlgeschlagen",
        "today_status_inactive": "Nicht aktiv",
        "today_saved_commute_generic": "Gespeicherte Verbindung",
        "today_departures_empty": "Keine Abfahrten in diesem Zeitfenster.",
        "today_departure_cancelled": "fällt aus",
        "today_departure_delay": "{delay} Min.",
        "today_platform": "Gleis {platform}",
        "today_platform_changed": "Gleis {platform} statt {planned_platform}",
        "today_alternative_label": "Alternative:",
        "notifications_heading": "Benachrichtigungen",
        "notifications_need_commute": "Speichere zuerst eine Verbindung, um Benachrichtigungen einzurichten.",
        "notifications_status_pending": "Verknüpfung ausstehend.",
        "notifications_telegram_heading": "Telegram",
        "notifications_telegram_button": "Telegram-Link erzeugen",
        "notifications_telegram_link_cta": "In Telegram öffnen",
        "notifications_telegram_unavailable": "Telegram ist gerade nicht konfiguriert. Bitte später erneut versuchen.",
        "notifications_ntfy_heading": "ntfy",
        "notifications_ntfy_label": "ntfy-Topic",
        "notifications_ntfy_submit": "Speichern",
        "notifications_ntfy_error": "Bitte ein gültiges Topic angeben (Buchstaben, Ziffern, _ oder -, max. 64 Zeichen).",
        "footer_impressum": "Impressum",
        "footer_datenschutz": "Datenschutz",
        "footer_about": "Über die App",
        "footer_language_switch": "English",
        "footer_nav_label": "Fußzeilen-Navigation",
        "impressum_heading": "Angaben gemäß § 5 DDG",
        "impressum_name_label": "Name",
        "impressum_address_label": "Adresse",
        "impressum_email_label": "E-Mail",
        "datenschutz_heading": "Datenschutzerklärung",
        "datenschutz_cookie_text": (
            "Beim ersten Speichern einer Verbindung setzt die App ein HttpOnly-Cookie "
            "namens uid mit einer zufälligen, undurchsichtigen Kennung (uid). Damit "
            "ordnen wir dir deine gespeicherten Daten zu."
        ),
        "datenschutz_data_text": (
            "Zu deiner Kennung speichern wir: deine gespeicherten Verbindungen "
            "(Start- und Ziel-Haltestelle, Linien, Wochentage, Abfahrtsfenster und "
            "der Verspätungs-Schwellenwert in Minuten [delay_threshold_min]); falls "
            "eingerichtet, die Telegram-Chat-ID oder das ntfy-Topic sowie ein "
            "einmaliges Verknüpfungs-Token während der Einrichtung; und den "
            "Benachrichtigungsstatus je Störung. Wir speichern keine E-Mail-Adresse, "
            "keinen Namen und verwenden keine Tracker."
        ),
        "datenschutz_lang_cookie_text": (
            "Ein weiteres Cookie namens lang speichert nur deine gewählte Sprache "
            "(Deutsch oder Englisch)."
        ),
        "datenschutz_delete_text": (
            "Über die Schaltfläche „Meine Daten löschen“ (POST /me/delete) entfernst "
            "du mit einem Klick alle deine Daten – alle Zeilen zu deiner Kennung "
            "sowie das uid-Cookie."
        ),
        "datenschutz_hafas_text": (
            "Um Haltestellen zu suchen und Abfahrten abzurufen, sendet die App "
            "Anfragen an die VBB/BVG-HAFAS-REST-API (v6.bvg.transport.rest)."
        ),
        "datenschutz_third_party_text": (
            "Falls du Benachrichtigungen eingerichtet hast, werden diese über die "
            "Drittanbieter Telegram und/oder den vom Betreiber konfigurierten "
            "ntfy-Server (an das von dir gewählte Topic) zugestellt."
        ),
        "datenschutz_ip_text": (
            "Zum Schutz vor Missbrauch verarbeitet die App deine IP-Adresse "
            "ausschließlich im Arbeitsspeicher des Servers, zusammen mit einem "
            "Anfragezähler; sie wird nicht in der Datenbank gespeichert und geht "
            "spätestens bei einem Neustart des Servers verloren."
        ),
        "about_heading": "Über die App",
        "about_free_text": "Mein Pendel ist kostenlos, ohne Werbung und ohne Tracking.",
        "about_kofi_text": "Unterstütze das Projekt freiwillig auf Ko-fi",
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
        "stops_origin_label": "Origin",
        "stops_destination_hint": "Now choose the destination stop.",
        "commute_new_heading": "Save a commute",
        "commute_new_missing_stops": "Please choose an origin and destination first.",
        "commute_new_origin_label": "Origin",
        "commute_new_destination_label": "Destination",
        "commute_new_lines_label": "Lines",
        "commute_new_lines_error": "Lines are temporarily unavailable. Please try again later.",
        "commute_new_retry_cta": "Try again",
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
        "commutes_heading": "My commutes",
        "delete_me_button": "Delete my data",
        "delete_me_done": "Your data has been deleted.",
        "today_heading": "Today on my route",
        "today_empty": "You have not saved a commute yet.",
        "today_empty_cta": "Search for a stop",
        "today_inactive": "Not active today.",
        "today_unavailable": "Status is temporarily unavailable.",
        "today_status_ok": "OK",
        "today_status_disrupted": "Disrupted",
        "today_status_paused": "Paused",
        "today_status_failed": "Checking failed",
        "today_status_inactive": "Not active",
        "today_saved_commute_generic": "Saved commute",
        "today_departures_empty": "No departures in this window.",
        "today_departure_cancelled": "cancelled",
        "today_departure_delay": "{delay} min",
        "today_platform": "Platform {platform}",
        "today_platform_changed": "Platform {platform} instead of {planned_platform}",
        "today_alternative_label": "Alternative:",
        "notifications_heading": "Notifications",
        "notifications_need_commute": "Save a commute first to set up notifications.",
        "notifications_status_pending": "Link pending.",
        "notifications_telegram_heading": "Telegram",
        "notifications_telegram_button": "Get a Telegram link",
        "notifications_telegram_link_cta": "Open in Telegram",
        "notifications_telegram_unavailable": "Telegram is not configured right now. Please try again later.",
        "notifications_ntfy_heading": "ntfy",
        "notifications_ntfy_label": "ntfy topic",
        "notifications_ntfy_submit": "Save",
        "notifications_ntfy_error": "Please enter a valid topic (letters, digits, _ or -, max 64 characters).",
        "footer_impressum": "Impressum",
        "footer_datenschutz": "Privacy Policy",
        "footer_about": "About",
        "footer_language_switch": "Deutsch",
        "footer_nav_label": "Footer navigation",
        "impressum_heading": "Information according to § 5 DDG (German Digital Services Act)",
        "impressum_name_label": "Name",
        "impressum_address_label": "Address",
        "impressum_email_label": "Email",
        "datenschutz_heading": "Privacy Policy",
        "datenschutz_cookie_text": (
            "When you save your first commute, the app sets an HttpOnly cookie "
            "named uid containing a random, opaque identifier (uid). It is used to "
            "associate your saved data with you."
        ),
        "datenschutz_data_text": (
            "Against your identifier we store: your saved commutes (origin and "
            "destination stop, lines, weekdays, departure window and the delay "
            "threshold in minutes [delay_threshold_min]); if set up, your Telegram "
            "chat id or ntfy topic and a one-time linking token while linking is "
            "pending; and the notification state per disruption. We store no email "
            "address, no name, and use no trackers."
        ),
        "datenschutz_lang_cookie_text": (
            "A separate cookie named lang stores only your chosen language "
            "(German or English)."
        ),
        "datenschutz_delete_text": (
            "The “Delete my data” button (POST /me/delete) removes all "
            "your data with one click – every row tied to your identifier and "
            "the uid cookie."
        ),
        "datenschutz_hafas_text": (
            "To search stops and fetch departures, the app sends requests to the "
            "VBB/BVG HAFAS REST API (v6.bvg.transport.rest)."
        ),
        "datenschutz_third_party_text": (
            "If you have set up notifications, they are delivered through the "
            "third-party services Telegram and/or the operator-configured ntfy "
            "server (to the topic you chose)."
        ),
        "datenschutz_ip_text": (
            "To protect against abuse, the app processes your IP address only in "
            "memory on the server, together with a request counter; it is not "
            "stored in the database and is lost at the latest when the server "
            "restarts."
        ),
        "about_heading": "About",
        "about_free_text": "Mein Pendel is free, with no ads and no tracking.",
        "about_kofi_text": "Support the project voluntarily on Ko-fi",
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

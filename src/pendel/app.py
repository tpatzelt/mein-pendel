"""FastAPI app skeleton (charter G2): server-rendered, mobile-first HTML, no JS build step.

`uv run uvicorn pendel.app:app` serves this. `/healthz` backs G4's container
healthcheck; `/` renders the base template so later G2 routes (stop search,
saved commutes, "today on my route") have a page to extend.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sqlite3
import threading
import urllib.parse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from pendel import db, telegram_link
from pendel.alternatives import Alternative, suggest_alternative
from pendel.commute import BERLIN, Commute
from pendel.engine import NextDeparture, Verdict, evaluate, line_choices, next_departures
from pendel.hafas import HafasClient, HafasError
from pendel.i18n import resolve_language, translate
from pendel.ratelimit import RateLimiter, rate_limit_per_min_from_env

_BASE_DIR = Path(__file__).parent
_MIN_QUERY_LENGTH = 2
_TELEGRAM_BOT_USERNAME_ENV = "PENDEL_TELEGRAM_BOT_USERNAME"
_NTFY_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# How far ahead to look for the origin's departing lines on /commutes/new
# (charter G1): long enough to see a representative set of lines, short
# enough to stay a single cheap HAFAS call.
_LINE_CHOICES_DURATION_MINUTES = 60
_DEFAULT_WEEKDAYS = frozenset({"0", "1", "2", "3", "4"})  # Mon-Fri
_DEFAULT_DELAY_THRESHOLD_MIN = "5"

# Decorative, aria-hidden icon per "today" card status (charter G3): status
# is never conveyed by class or color alone, so every card also carries a
# visible localized label (see `_status_item`).
_STATUS_ICONS = {
    "ok": "✓",
    "disrupted": "⚠",
    "paused": "⏸",
    "failed": "✕",
    "inactive": "○",
}

# HafasClient's TTL cache is a plain OrderedDict, not thread-safe; sync
# endpoints run in a threadpool, so serialize access to the shared client.
_hafas_lock = threading.Lock()

templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))

_RATE_LIMIT_EXEMPT_PATH = "/healthz"
_RATE_LIMIT_EXEMPT_STATIC_PREFIX = "/static/"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the process-wide `RateLimiter` from `PENDEL_RATE_LIMIT_PER_MIN`
    (charter G5) and tear it down on exit.

    Only this lifespan sets `app.state.rate_limiter`, and the try/finally
    always resets it to None on exit -- so a bare `TestClient(app)` used
    without `with` in other test modules never triggers it, and a `with
    TestClient(app)` block never leaks a limiter onto the shared `app`
    object past its own `with` block.
    """
    app.state.rate_limiter = RateLimiter(limit_per_min=rate_limit_per_min_from_env())
    try:
        yield
    finally:
        app.state.rate_limiter = None


app = FastAPI(title="Mein Pendel", lifespan=lifespan)
app.state.rate_limiter = None
app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next: Any) -> Any:
    """Return 429 with Retry-After once `app.state.rate_limiter` (set only
    by `lifespan`) reports the caller's key over PENDEL_RATE_LIMIT_PER_MIN
    for the current window. See `pendel.ratelimit` for why the key is
    `request.client.host` and not a proxy header."""
    limiter: RateLimiter | None = request.app.state.rate_limiter
    path = request.url.path
    if (
        limiter is not None
        and path != _RATE_LIMIT_EXEMPT_PATH
        and not path.startswith(_RATE_LIMIT_EXEMPT_STATIC_PREFIX)
    ):
        client = request.client
        key = client.host if client is not None else "unknown"
        result = limiter.check(key)
        if not result.allowed:
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(result.retry_after)},
            )
    return await call_next(request)


def _language_for(request: Request) -> str:
    return resolve_language(
        request.query_params.get("lang"),
        request.cookies.get("lang"),
        request.headers.get("accept-language"),
    )


@lru_cache(maxsize=1)
def get_hafas_client() -> HafasClient:
    """One shared HafasClient per process, so its TTL cache is actually
    reused across requests instead of starting empty every time."""
    return HafasClient(httpx.Client(timeout=10.0))


def get_now() -> dt.datetime:
    """The current time, as its own dependency so tests can override it via
    `app.dependency_overrides` instead of freezing the real clock."""
    return dt.datetime.now(BERLIN)


def _fetch_departures(
    hafas_client: HafasClient, stop_id: str, when: dt.datetime, duration_minutes: int
) -> Any:
    """Sync HAFAS call, run off the event loop (see `today`) via
    `run_in_threadpool`; takes `_hafas_lock` like the sync `/stops` route
    does, since they share one HafasClient."""
    with _hafas_lock:
        return hafas_client.departures(stop_id, when, duration_minutes)


def _fetch_journeys(
    hafas_client: HafasClient, origin_stop_id: str, destination_stop_id: str, when: dt.datetime
) -> Any:
    """Sync HAFAS call for a disrupted commute's suggested alternative (see
    `today`), run off the event loop via `run_in_threadpool` under the same
    `_hafas_lock` as `_fetch_departures` since both share one HafasClient."""
    with _hafas_lock:
        return hafas_client.journeys(origin_stop_id, destination_stop_id, when)


def _default_window(now: dt.datetime) -> tuple[dt.time, dt.time]:
    """Default departure window (charter G1): starts at the next full half
    hour after `now`, wrapping past midnight (e.g. 23:45 gives 00:00, not
    23:30); window_end is start + 30 min, wrapping past midnight the same
    way (e.g. 23:30 gives 00:00) since a window crossing midnight is valid
    (charter G2) and `Commute` already supports it.
    """
    local = now.astimezone(BERLIN)
    total_minutes = local.hour * 60 + local.minute
    remainder = total_minutes % 30
    if remainder == 0 and local.second == 0 and local.microsecond == 0:
        start_minutes = total_minutes
    else:
        start_minutes = total_minutes - remainder + 30
    start_minutes %= 24 * 60
    window_start = dt.time(start_minutes // 60, start_minutes % 60)
    end_minutes = (start_minutes + 30) % (24 * 60)
    window_end = dt.time(end_minutes // 60, end_minutes % 60)
    return window_start, window_end


def _today_window(
    commute: Commute, now: dt.datetime, today_date: dt.date
) -> tuple[dt.datetime, dt.datetime] | None:
    """The departure window `/today` should fetch and evaluate `commute`
    against, or None when the commute is inactive.

    Prefers the window `now` currently falls into (`Commute.window_containing`,
    same lookup `evaluate` uses), so a window crossing midnight (e.g.
    23:30-00:30) that started yesterday and is still open still counts as
    active (charter G2). Falls back to today's window when the commute runs
    today but `now` isn't inside any window yet (e.g. before it opens).
    """
    window = commute.window_containing(now)
    if window is not None:
        return window
    if commute.is_active_on(today_date):
        return commute.window_bounds(today_date)
    return None


async def _line_choices_for_origin(
    hafas_client: HafasClient, origin_stop_id: str, now: dt.datetime
) -> tuple[list[str], bool]:
    """The origin's line choices for the setup checkboxes (charter G1), and
    whether they are unavailable (HafasError, or HAFAS returned no lines).
    Never falls back to a text field -- callers show a retry link instead."""
    try:
        departures_json = await run_in_threadpool(
            _fetch_departures, hafas_client, origin_stop_id, now, _LINE_CHOICES_DURATION_MINUTES
        )
    except HafasError:
        return [], True
    choices = line_choices(departures_json)
    return choices, not choices


async def get_db() -> AsyncIterator[sqlite3.Connection]:
    """A migrated connection to the SQLite database at PENDEL_DATA_DIR,
    closed once the request is done.

    An async generator so this dependency and the async `/commutes` route
    that uses it run on the same (event-loop) thread as the sqlite3
    connection was created on -- a sync dependency would run in FastAPI's
    threadpool instead and sqlite3 rejects cross-thread use.
    """
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    language = _language_for(request)

    def t(key: str) -> str:
        return translate(language, key)

    response = templates.TemplateResponse(
        request,
        "index.html",
        {"language": language, "t": t, "deleted": request.query_params.get("deleted") == "1"},
    )
    if request.query_params.get("lang") in ("de", "en"):
        response.set_cookie("lang", language, samesite="lax")
    return response


def _render_static_page(request: Request, template_name: str) -> HTMLResponse:
    """Shared handler for the legal/about pages (charter G5): same language
    handling as '/', no other dynamic context."""
    language = _language_for(request)

    def t(key: str) -> str:
        return translate(language, key)

    response = templates.TemplateResponse(
        request,
        template_name,
        {"language": language, "t": t},
    )
    if request.query_params.get("lang") in ("de", "en"):
        response.set_cookie("lang", language, samesite="lax")
    return response


@app.get("/impressum", response_class=HTMLResponse)
def impressum(request: Request) -> HTMLResponse:
    return _render_static_page(request, "impressum.html")


@app.get("/datenschutz", response_class=HTMLResponse)
def datenschutz(request: Request) -> HTMLResponse:
    return _render_static_page(request, "datenschutz.html")


@app.get("/about", response_class=HTMLResponse)
def about(request: Request) -> HTMLResponse:
    return _render_static_page(request, "about.html")


@app.get("/stops", response_class=HTMLResponse)
def stops(
    request: Request,
    q: str = "",
    origin_stop_id: str = "",
    origin_name: str = "",
    hafas_client: HafasClient = Depends(get_hafas_client),
) -> HTMLResponse:
    language = _language_for(request)

    def t(key: str) -> str:
        return translate(language, key)

    query = q.strip()
    results: list[dict[str, str]] = []
    error_message: str | None = None
    status_code = 200

    if len(query) >= _MIN_QUERY_LENGTH:
        try:
            with _hafas_lock:
                raw_locations = hafas_client.locations(query)
        except HafasError:
            error_message = t("stops_error")
            status_code = 503
        else:
            results = [
                {"id": entry["id"], "name": entry["name"]}
                for entry in raw_locations
                if entry.get("type") in ("stop", "station") and entry.get("id") and entry.get("name")
            ]

    response = templates.TemplateResponse(
        request,
        "stops.html",
        {
            "language": language,
            "t": t,
            "query": query,
            "origin_stop_id": origin_stop_id,
            "origin_name": origin_name,
            "results": results,
            "error_message": error_message,
        },
        status_code=status_code,
    )
    if request.query_params.get("lang") in ("de", "en"):
        response.set_cookie("lang", language, samesite="lax")
    return response


@app.get("/commutes/new", response_class=HTMLResponse)
async def commutes_new_form(
    request: Request,
    hafas_client: HafasClient = Depends(get_hafas_client),
    now: dt.datetime = Depends(get_now),
) -> HTMLResponse:
    """Setup screen 3 (charter G1): line checkboxes from the origin's own
    departures, never a typed stop id or line name. Requires all four of
    origin/destination stop id and name (carried as query params from the
    /stops flow); without them this links back to /stops without calling
    HAFAS."""
    language = _language_for(request)

    def t(key: str) -> str:
        return translate(language, key)

    origin_stop_id = request.query_params.get("origin_stop_id", "")
    origin_name = request.query_params.get("origin_name", "")
    destination_stop_id = request.query_params.get("destination_stop_id", "")
    destination_name = request.query_params.get("destination_name", "")
    ready = bool(origin_stop_id and origin_name and destination_stop_id and destination_name)

    context: dict[str, Any] = {"language": language, "t": t, "ready": ready}
    status_code = 200

    if ready:
        choices, unavailable = await _line_choices_for_origin(hafas_client, origin_stop_id, now)
        window_start, window_end = _default_window(now)
        status_code = 503 if unavailable else 200
        context |= {
            "origin_stop_id": origin_stop_id,
            "origin_name": origin_name,
            "destination_stop_id": destination_stop_id,
            "destination_name": destination_name,
            "line_choices": choices,
            "selected_lines": frozenset(),
            "lines_unavailable": unavailable,
            "selected_weekdays": _DEFAULT_WEEKDAYS,
            "window_start": window_start.strftime("%H:%M"),
            "window_end": window_end.strftime("%H:%M"),
            "delay_threshold_min": _DEFAULT_DELAY_THRESHOLD_MIN,
            "error_message": None,
        }

    response = templates.TemplateResponse(
        request, "commute_new.html", context, status_code=status_code
    )
    if request.query_params.get("lang") in ("de", "en"):
        response.set_cookie("lang", language, samesite="lax")
    return response


@app.post("/commutes")
async def commutes_create(
    request: Request,
    db_conn: sqlite3.Connection = Depends(get_db),
    hafas_client: HafasClient = Depends(get_hafas_client),
    now: dt.datetime = Depends(get_now),
) -> HTMLResponse:
    language = _language_for(request)

    def t(key: str) -> str:
        return translate(language, key)

    # request.form() needs the python-multipart package, which is not a
    # dependency of this project; the form has no file inputs, so the
    # urlencoded body is parsed directly instead.
    body = await request.body()
    form = urllib.parse.parse_qs(body.decode("utf-8"), keep_blank_values=True)
    origin_stop_id = form.get("origin_stop_id", [""])[0].strip()
    origin_name = form.get("origin_name", [""])[0].strip()
    destination_stop_id = form.get("destination_stop_id", [""])[0].strip()
    destination_name = form.get("destination_name", [""])[0].strip()
    selected_lines = [line.strip() for line in form.get("lines", []) if line.strip()]
    weekdays_raw = form.get("weekdays", [])
    window_start_raw = form.get("window_start", [""])[0].strip()
    window_end_raw = form.get("window_end", [""])[0].strip()
    delay_raw = form.get("delay_threshold_min", [""])[0].strip()

    commute: Commute | None = None
    try:
        if not (origin_stop_id and origin_name and destination_stop_id and destination_name):
            raise ValueError("origin and destination stop id and name are required")
        commute = Commute(
            origin_stop_id=origin_stop_id,
            destination_stop_id=destination_stop_id,
            origin_name=origin_name,
            destination_name=destination_name,
            lines=frozenset(selected_lines),
            weekdays=frozenset(int(day) for day in weekdays_raw),
            window_start=dt.time.fromisoformat(window_start_raw),
            window_end=dt.time.fromisoformat(window_end_raw),
            delay_threshold_min=int(delay_raw) if delay_raw else 5,
        )
    except (ValueError, TypeError):
        commute = None

    if commute is None:
        ready = bool(origin_stop_id and origin_name and destination_stop_id and destination_name)
        context: dict[str, Any] = {"language": language, "t": t, "ready": ready}
        if ready:
            choices, unavailable = await _line_choices_for_origin(hafas_client, origin_stop_id, now)
            context |= {
                "origin_stop_id": origin_stop_id,
                "origin_name": origin_name,
                "destination_stop_id": destination_stop_id,
                "destination_name": destination_name,
                "line_choices": choices,
                "selected_lines": frozenset(selected_lines),
                "lines_unavailable": unavailable,
                "selected_weekdays": frozenset(weekdays_raw),
                "window_start": window_start_raw,
                "window_end": window_end_raw,
                "delay_threshold_min": delay_raw or _DEFAULT_DELAY_THRESHOLD_MIN,
                "error_message": t("commute_new_error"),
            }
        return templates.TemplateResponse(
            request, "commute_new.html", context, status_code=400
        )

    uid = request.cookies.get("uid")
    if uid is None or not db.user_exists(db_conn, uid):
        uid = db.create_user(db_conn)

    db.add_commute(db_conn, uid, commute)

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("uid", uid, httponly=True, samesite="lax", path="/")
    return response


def _commute_title(commute: Commute, t: Any) -> str:
    """The card heading (charter G3): 'origin name → destination name',
    never a stop id. Old commutes saved before stop names were stored have
    empty origin_name/destination_name; those get a localized generic
    label instead of an empty arrow."""
    if commute.origin_name and commute.destination_name:
        return f"{commute.origin_name} → {commute.destination_name}"
    return t("today_saved_commute_generic")


def _platform_text(t: Any, platform: str | None, planned_platform: str | None) -> str | None:
    """Charter G3's platform-change wording ('Gleis 3 statt 1'): shown only
    when both the real-time and planned platform are known and differ, so a
    single reported platform is never misread as a change."""
    if platform is not None and planned_platform is not None and platform != planned_platform:
        return t("today_platform_changed").format(platform=platform, planned_platform=planned_platform)
    display = platform if platform is not None else planned_platform
    if display is None:
        return None
    return t("today_platform").format(platform=display)


def _departure_item(departure: NextDeparture, t: Any) -> dict[str, Any]:
    """One <li> for the "today" card's departures list (charter G3):
    planned time always shown; real-time time and delay text only when the
    delay is known and non-zero (a cancelled departure shows neither)."""
    planned_local = departure.planned.astimezone(BERLIN)
    show_realtime = (
        not departure.cancelled and departure.realtime is not None and departure.delay_min
    )
    realtime_local = departure.realtime.astimezone(BERLIN) if show_realtime else None
    return {
        "line": departure.line,
        "planned_iso": planned_local.isoformat(),
        "planned_time": planned_local.strftime("%H:%M"),
        "cancelled": departure.cancelled,
        "cancelled_label": t("today_departure_cancelled") if departure.cancelled else None,
        "realtime_iso": realtime_local.isoformat() if realtime_local else None,
        "realtime_time": realtime_local.strftime("%H:%M") if realtime_local else None,
        "delay_text": (
            t("today_departure_delay").format(delay=f"{departure.delay_min:+d}")
            if show_realtime
            else None
        ),
        "platform_text": _platform_text(t, departure.platform, departure.planned_platform),
    }


def _status_item(
    commute_id: int,
    commute: Commute,
    t: Any,
    status: str,
    message: str | None,
    departures: list[dict[str, Any]] | None = None,
    alternative: str | None = None,
) -> dict[str, Any]:
    return {
        "commute_id": commute_id,
        "title": _commute_title(commute, t),
        "status": status,
        "status_label": t(f"today_status_{status}"),
        "status_icon": _STATUS_ICONS[status],
        "message": message,
        "departures": departures,
        "alternative": alternative,
    }


async def _alternative_text(
    hafas_client: HafasClient,
    commute: Commute,
    verdict: Verdict,
    now: dt.datetime,
    language: str,
) -> str | None:
    """The suggested alternative's localized summary for a disrupted card
    (charter G3), or None when HAFAS errors or no alternative qualifies --
    either way the card stays disrupted, never an error page. One
    `/journeys` call, only for a commute the engine already found affected.
    """
    try:
        journeys_json = await run_in_threadpool(
            _fetch_journeys,
            hafas_client,
            commute.origin_stop_id,
            commute.destination_stop_id,
            now,
        )
    except HafasError:
        return None
    alternative: Alternative | None = suggest_alternative(commute, verdict, journeys_json)
    if alternative is None:
        return None
    return alternative.summary_de if language == "de" else alternative.summary_en


_WEEKDAY_KEYS = (
    "weekday_mon",
    "weekday_tue",
    "weekday_wed",
    "weekday_thu",
    "weekday_fri",
    "weekday_sat",
    "weekday_sun",
)


def _weekdays_text(weekdays: frozenset[int], t: Any) -> str:
    """Localized weekday abbreviations in calendar order (charter G2's 'my
    commutes' list), e.g. 'Mo, Mi, Fr'."""
    return ", ".join(t(_WEEKDAY_KEYS[day]) for day in sorted(weekdays))


def _commute_list_item(commute: Commute, t: Any) -> dict[str, Any]:
    """One row for the 'my commutes' list (charter G2): name, sorted lines,
    localized weekdays and the window as HH:MM-HH:MM, never a stop id."""
    return {
        "title": _commute_title(commute, t),
        "lines_text": ", ".join(sorted(commute.lines)),
        "weekdays_text": _weekdays_text(commute.weekdays, t),
        "window_text": f"{commute.window_start:%H:%M}–{commute.window_end:%H:%M}",
        "paused": commute.paused,
    }


@app.get("/commutes", response_class=HTMLResponse)
async def commutes_list(
    request: Request,
    db_conn: sqlite3.Connection = Depends(get_db),
) -> HTMLResponse:
    """'My commutes' (charter G2): every saved commute for the uid cookie's
    user, listed by name, lines, weekdays and window; without a user (or
    with no saved commutes), the empty state links to /stops to start G1's
    setup flow."""
    language = _language_for(request)

    def t(key: str) -> str:
        return translate(language, key)

    uid = request.cookies.get("uid")
    has_user = uid is not None and db.user_exists(db_conn, uid)
    items = (
        [_commute_list_item(commute, t) for _commute_id, commute in db.list_commutes(db_conn, uid)]
        if has_user
        else []
    )

    response = templates.TemplateResponse(
        request,
        "commutes.html",
        {"language": language, "t": t, "has_user": has_user, "items": items},
    )
    if request.query_params.get("lang") in ("de", "en"):
        response.set_cookie("lang", language, samesite="lax")
    return response


@app.get("/today", response_class=HTMLResponse)
async def today(
    request: Request,
    db_conn: sqlite3.Connection = Depends(get_db),
    hafas_client: HafasClient = Depends(get_hafas_client),
    now: dt.datetime = Depends(get_now),
) -> HTMLResponse:
    """'Today on my route' (charter G2): each active saved commute's engine
    verdict for today, without ever fetching journeys.

    sqlite access stays on the event-loop thread like the rest of this
    module's async routes (sqlite3 connections reject cross-thread use).
    The HAFAS call is the only blocking part -- it can wait on the public
    instance's rate limit or on `_hafas_lock` -- so it alone is pushed to
    the threadpool via `run_in_threadpool`.
    """
    language = _language_for(request)

    def t(key: str) -> str:
        return translate(language, key)

    uid = request.cookies.get("uid")
    has_user = uid is not None and db.user_exists(db_conn, uid)

    items: list[dict[str, Any]] = []
    if has_user:
        commutes = db.list_commutes(db_conn, uid)
        today_date = now.astimezone(BERLIN).date()

        # One departures fetch per origin stop covers every active,
        # non-paused commute from that stop, widened to the earliest
        # start / latest end among them so evaluate()'s per-commute window
        # filtering still applies. A paused commute's origin is never
        # fetched (charter G3: a paused-only user makes zero HAFAS calls).
        windows_by_stop: dict[str, tuple[dt.datetime, dt.datetime]] = {}
        commute_windows: dict[int, tuple[dt.datetime, dt.datetime] | None] = {}
        for commute_id, commute in commutes:
            if commute.paused:
                continue
            window = _today_window(commute, now, today_date)
            commute_windows[commute_id] = window
            if window is None:
                continue
            start, end = window
            existing = windows_by_stop.get(commute.origin_stop_id)
            windows_by_stop[commute.origin_stop_id] = (
                (start, end)
                if existing is None
                else (min(existing[0], start), max(existing[1], end))
            )

        departures_by_stop: dict[str, dict[str, Any] | None] = {}
        for stop_id, (start, end) in windows_by_stop.items():
            duration_minutes = max(1, int((end - start).total_seconds() // 60) + 1)
            try:
                departures_by_stop[stop_id] = await run_in_threadpool(
                    _fetch_departures, hafas_client, stop_id, start, duration_minutes
                )
            except HafasError:
                departures_by_stop[stop_id] = None

        for commute_id, commute in commutes:
            if commute.paused:
                items.append(_status_item(commute_id, commute, t, "paused", None))
                continue

            if commute_windows.get(commute_id) is None:
                items.append(
                    _status_item(commute_id, commute, t, "inactive", t("today_inactive"))
                )
                continue

            departures_json = departures_by_stop.get(commute.origin_stop_id)
            if departures_json is None:
                items.append(
                    _status_item(commute_id, commute, t, "failed", t("today_unavailable"))
                )
                continue

            verdict = evaluate(
                commute, departures_json, now, delay_threshold_min=commute.delay_threshold_min
            )
            message = verdict.reason_de if language == "de" else verdict.reason_en
            departures = [
                _departure_item(departure, t)
                for departure in next_departures(commute, departures_json, now)
            ]
            alternative = (
                await _alternative_text(hafas_client, commute, verdict, now, language)
                if verdict.affected
                else None
            )
            items.append(
                _status_item(
                    commute_id,
                    commute,
                    t,
                    "disrupted" if verdict.affected else "ok",
                    message,
                    departures,
                    alternative,
                )
            )

    response = templates.TemplateResponse(
        request,
        "today.html",
        {
            "language": language,
            "t": t,
            "has_user": has_user,
            "items": items,
        },
    )
    if request.query_params.get("lang") in ("de", "en"):
        response.set_cookie("lang", language, samesite="lax")
    return response


@app.post("/me/delete")
async def me_delete(
    request: Request,
    db_conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    """Delete-my-data (charter G5): remove every row for the `uid` cookie's
    user, via db.delete_user's ON DELETE CASCADE, and clear the cookie."""
    uid = request.cookies.get("uid")
    if uid is not None and db.user_exists(db_conn, uid):
        db.delete_user(db_conn, uid)

    response = RedirectResponse(url="/?deleted=1", status_code=303)
    response.delete_cookie("uid", path="/")
    return response


def _mask_target(target: str) -> str:
    """Mask a linked channel's target (chat id or ntfy topic) for display,
    e.g. so a screen-shared page never shows the full value."""
    if len(target) <= 4:
        return "•" * len(target)
    return "•" * (len(target) - 4) + target[-4:]


def _notifications_context(
    db_conn: sqlite3.Connection, uid: str | None, has_user: bool, t: Any
) -> dict[str, Any]:
    """Shared context for GET /notifications and the error/hint paths of its
    POST routes: the user's linked channels and, if a telegram channel is
    still pending, its deep link (rebuilt from the stored link_token)."""
    channels = db.list_channels(db_conn, uid) if has_user else []
    bot_username = os.environ.get(_TELEGRAM_BOT_USERNAME_ENV)
    telegram_link_url = None
    if bot_username:
        for row in channels:
            if row["kind"] == "telegram" and row["link_token"]:
                telegram_link_url = f"https://t.me/{bot_username}?start={row['link_token']}"
                break
    return {
        "t": t,
        "has_user": has_user,
        "channels": [
            {
                "kind": row["kind"],
                "linked": row["linked_at"] is not None,
                "masked_target": _mask_target(row["target"]) if row["target"] else "",
            }
            for row in channels
        ],
        "telegram_link_url": telegram_link_url,
    }


@app.get("/notifications", response_class=HTMLResponse)
async def notifications_page(
    request: Request, db_conn: sqlite3.Connection = Depends(get_db)
) -> HTMLResponse:
    language = _language_for(request)

    def t(key: str) -> str:
        return translate(language, key)

    uid = request.cookies.get("uid")
    has_user = uid is not None and db.user_exists(db_conn, uid)
    context = {"language": language, **_notifications_context(db_conn, uid, has_user, t)}
    response = templates.TemplateResponse(request, "notifications.html", context)
    if request.query_params.get("lang") in ("de", "en"):
        response.set_cookie("lang", language, samesite="lax")
    return response


@app.post("/notifications/telegram")
async def notifications_telegram(request: Request, db_conn: sqlite3.Connection = Depends(get_db)):
    language = _language_for(request)

    def t(key: str) -> str:
        return translate(language, key)

    uid = request.cookies.get("uid")
    has_user = uid is not None and db.user_exists(db_conn, uid)
    if not has_user:
        return RedirectResponse(url="/notifications", status_code=303)

    try:
        telegram_link.create_link(db_conn, uid)
    except telegram_link.TelegramLinkConfigError:
        context = {"language": language, **_notifications_context(db_conn, uid, has_user, t)}
        context["telegram_error"] = t("notifications_telegram_unavailable")
        return templates.TemplateResponse(
            request, "notifications.html", context, status_code=503
        )

    return RedirectResponse(url="/notifications", status_code=303)


@app.post("/notifications/ntfy")
async def notifications_ntfy(request: Request, db_conn: sqlite3.Connection = Depends(get_db)):
    language = _language_for(request)

    def t(key: str) -> str:
        return translate(language, key)

    body = await request.body()
    form = urllib.parse.parse_qs(body.decode("utf-8"), keep_blank_values=True)
    topic = form.get("topic", [""])[0].strip()

    uid = request.cookies.get("uid")
    has_user = uid is not None and db.user_exists(db_conn, uid)
    if not has_user:
        return RedirectResponse(url="/notifications", status_code=303)

    if not _NTFY_TOPIC_RE.match(topic):
        context = {"language": language, **_notifications_context(db_conn, uid, has_user, t)}
        context["ntfy_error"] = t("notifications_ntfy_error")
        context["ntfy_topic"] = topic
        return templates.TemplateResponse(
            request, "notifications.html", context, status_code=400
        )

    db.add_ntfy_channel(db_conn, uid, topic)
    return RedirectResponse(url="/notifications", status_code=303)

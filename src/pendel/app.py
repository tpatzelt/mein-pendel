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
from pendel.commute import BERLIN, Commute
from pendel.engine import evaluate
from pendel.hafas import HafasClient, HafasError
from pendel.i18n import resolve_language, translate

_BASE_DIR = Path(__file__).parent
_MIN_QUERY_LENGTH = 2
_TELEGRAM_BOT_USERNAME_ENV = "PENDEL_TELEGRAM_BOT_USERNAME"
_NTFY_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# HafasClient's TTL cache is a plain OrderedDict, not thread-safe; sync
# endpoints run in a threadpool, so serialize access to the shared client.
_hafas_lock = threading.Lock()

templates = Jinja2Templates(directory=str(_BASE_DIR / "templates"))

app = FastAPI(title="Mein Pendel")
app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")


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


@app.get("/stops", response_class=HTMLResponse)
def stops(
    request: Request,
    q: str = "",
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
            "results": results,
            "error_message": error_message,
        },
        status_code=status_code,
    )
    if request.query_params.get("lang") in ("de", "en"):
        response.set_cookie("lang", language, samesite="lax")
    return response


@app.get("/commutes/new", response_class=HTMLResponse)
def commutes_new_form(request: Request) -> HTMLResponse:
    language = _language_for(request)

    def t(key: str) -> str:
        return translate(language, key)

    response = templates.TemplateResponse(
        request,
        "commute_new.html",
        {
            "language": language,
            "t": t,
            "origin_stop_id": request.query_params.get("origin_stop_id", ""),
            "destination_stop_id": request.query_params.get("destination_stop_id", ""),
            "lines": "",
            "selected_weekdays": frozenset(),
            "window_start": "",
            "window_end": "",
            "delay_threshold_min": "5",
            "error_message": None,
        },
    )
    if request.query_params.get("lang") in ("de", "en"):
        response.set_cookie("lang", language, samesite="lax")
    return response


@app.post("/commutes")
async def commutes_create(
    request: Request,
    db_conn: sqlite3.Connection = Depends(get_db),
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
    destination_stop_id = form.get("destination_stop_id", [""])[0].strip()
    lines_raw = form.get("lines", [""])[0].strip()
    weekdays_raw = form.get("weekdays", [])
    window_start_raw = form.get("window_start", [""])[0].strip()
    window_end_raw = form.get("window_end", [""])[0].strip()
    delay_raw = form.get("delay_threshold_min", [""])[0].strip()

    commute: Commute | None = None
    try:
        if not origin_stop_id or not destination_stop_id:
            raise ValueError("origin and destination stop ids are required")
        commute = Commute(
            origin_stop_id=origin_stop_id,
            destination_stop_id=destination_stop_id,
            lines=frozenset(part.strip() for part in lines_raw.split(",") if part.strip()),
            weekdays=frozenset(int(day) for day in weekdays_raw),
            window_start=dt.time.fromisoformat(window_start_raw),
            window_end=dt.time.fromisoformat(window_end_raw),
            delay_threshold_min=int(delay_raw) if delay_raw else 5,
        )
    except (ValueError, TypeError):
        commute = None

    if commute is None:
        return templates.TemplateResponse(
            request,
            "commute_new.html",
            {
                "language": language,
                "t": t,
                "origin_stop_id": origin_stop_id,
                "destination_stop_id": destination_stop_id,
                "lines": lines_raw,
                "selected_weekdays": frozenset(weekdays_raw),
                "window_start": window_start_raw,
                "window_end": window_end_raw,
                "delay_threshold_min": delay_raw or "5",
                "error_message": t("commute_new_error"),
            },
            status_code=400,
        )

    uid = request.cookies.get("uid")
    if uid is None or not db.user_exists(db_conn, uid):
        uid = db.create_user(db_conn)

    db.add_commute(db_conn, uid, commute)

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("uid", uid, httponly=True, samesite="lax", path="/")
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

        # One departures fetch per origin stop covers every active commute
        # from that stop, widened to the earliest start / latest end among
        # them so evaluate()'s per-commute window filtering still applies.
        windows_by_stop: dict[str, tuple[dt.datetime, dt.datetime]] = {}
        for _, commute in commutes:
            if not commute.is_active_on(today_date):
                continue
            start, end = commute.window_bounds(today_date)
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
            if not commute.is_active_on(today_date):
                items.append(
                    {
                        "commute_id": commute_id,
                        "origin_stop_id": commute.origin_stop_id,
                        "destination_stop_id": commute.destination_stop_id,
                        "status": "inactive",
                        "message": t("today_inactive"),
                    }
                )
                continue

            departures_json = departures_by_stop.get(commute.origin_stop_id)
            if departures_json is None:
                items.append(
                    {
                        "commute_id": commute_id,
                        "origin_stop_id": commute.origin_stop_id,
                        "destination_stop_id": commute.destination_stop_id,
                        "status": "unavailable",
                        "message": t("today_unavailable"),
                    }
                )
                continue

            verdict = evaluate(
                commute, departures_json, now, delay_threshold_min=commute.delay_threshold_min
            )
            items.append(
                {
                    "commute_id": commute_id,
                    "origin_stop_id": commute.origin_stop_id,
                    "destination_stop_id": commute.destination_stop_id,
                    "status": "affected" if verdict.affected else "unaffected",
                    "message": verdict.reason_de if language == "de" else verdict.reason_en,
                }
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

"""FastAPI app skeleton (charter G2): server-rendered, mobile-first HTML, no JS build step.

`uv run uvicorn pendel.app:app` serves this. `/healthz` backs G4's container
healthcheck; `/` renders the base template so later G2 routes (stop search,
saved commutes, "today on my route") have a page to extend.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import threading
import urllib.parse
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pendel import db
from pendel.commute import Commute
from pendel.hafas import HafasClient, HafasError
from pendel.i18n import resolve_language, translate

_BASE_DIR = Path(__file__).parent
_MIN_QUERY_LENGTH = 2

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

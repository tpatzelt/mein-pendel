"""FastAPI app skeleton (charter G2): server-rendered, mobile-first HTML, no JS build step.

`uv run uvicorn pendel.app:app` serves this. `/healthz` backs G4's container
healthcheck; `/` renders the base template so later G2 routes (stop search,
saved commutes, "today on my route") have a page to extend.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
        {"language": language, "t": t},
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

"""HAFAS REST API client (v6.bvg.transport.rest): bounded TTL cache, 429/503 backoff.

Charter G1: the public HAFAS instance is rate-limited to about 100 req/min,
so production code must cache responses and back off on 429/503 instead of
hammering it. The httpx.Client, clock and sleep are all injected so tests
never touch the network or sleep for real.
"""

from __future__ import annotations

import copy
import datetime as dt
import math
import os
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

BERLIN = ZoneInfo("Europe/Berlin")

_DEFAULT_BASE_URL = "https://v6.bvg.transport.rest"
_RETRY_DELAYS = (0.5, 1.0, 2.0)
_RETRY_AFTER_CAP = 10.0
_RETRYABLE_STATUS = (429, 503)


class HafasError(Exception):
    """Any HAFAS REST API failure: HTTP error, transport error, invalid
    JSON, or retries exhausted after repeated 429/503."""


def cache_key(path: str, params: Mapping[str, Any]) -> tuple[str, tuple[tuple[str, Any], ...]]:
    """Cache key for a request: path plus params sorted by name, so param
    insertion order never affects the key."""
    return (path, tuple(sorted(params.items())))


def _to_berlin_iso(value: dt.datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(BERLIN).isoformat()


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Numeric Retry-After value in seconds, capped at 10s. Anything that
    is not a finite, non-negative number (missing, an HTTP-date, '-5',
    'nan', 'inf', garbage) is treated as absent."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, _RETRY_AFTER_CAP)


class HafasClient:
    """Client for v6.bvg.transport.rest with a bounded TTL cache and
    429/503 backoff."""

    def __init__(
        self,
        http_client: httpx.Client,
        *,
        base_url: str | None = None,
        ttl_seconds: float = 30.0,
        max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http_client
        self._base_url = (
            base_url or os.environ.get("PENDEL_HAFAS_BASE_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._sleep = sleep
        self._cache: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()

    def locations(self, query: str) -> Any:
        return self._get("/locations", {"query": query})

    def departures(self, stop_id: str, when: dt.datetime, duration: int) -> Any:
        params: dict[str, Any] = {"when": _to_berlin_iso(when), "duration": duration}
        return self._get(f"/stops/{stop_id}/departures", params)

    def journeys(self, from_id: str, to_id: str, departure: dt.datetime) -> Any:
        params: dict[str, Any] = {
            "from": from_id,
            "to": to_id,
            "departure": _to_berlin_iso(departure),
        }
        return self._get("/journeys", params)

    def trip(self, trip_id: str) -> Any:
        return self._get(f"/trips/{quote(trip_id, safe='')}", {})

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        key = cache_key(path, params)
        cached = self._cache_get(key)
        if cached is not None:
            return copy.deepcopy(cached)
        value = self._send_with_retry(path, params)
        self._cache_set(key, value)
        return copy.deepcopy(value)

    def _cache_get(self, key: tuple) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= self._clock():
            del self._cache[key]
            return None
        return value

    def _cache_set(self, key: tuple, value: Any) -> None:
        now = self._clock()
        for existing_key, (expires_at, _value) in list(self._cache.items()):
            if expires_at <= now:
                del self._cache[existing_key]
        self._cache[key] = (now + self._ttl_seconds, value)
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    def _send_with_retry(self, path: str, params: dict[str, Any]) -> Any:
        url = f"{self._base_url}{path}"
        attempt = 0
        while True:
            try:
                response = self._http.get(url, params=params)
            except httpx.TransportError as exc:
                raise HafasError(f"transport error requesting {path}") from exc
            if response.status_code in _RETRYABLE_STATUS:
                if attempt >= len(_RETRY_DELAYS):
                    raise HafasError(
                        f"exhausted retries for {path} (status {response.status_code})"
                    )
                delay = _retry_after_seconds(response)
                if delay is None:
                    delay = _RETRY_DELAYS[attempt]
                self._sleep(delay)
                attempt += 1
                continue
            if response.status_code >= 400:
                raise HafasError(f"HTTP {response.status_code} requesting {path}")
            try:
                return response.json()
            except ValueError as exc:
                raise HafasError(f"invalid JSON from {path}") from exc

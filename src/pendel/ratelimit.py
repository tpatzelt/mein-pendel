"""Per-IP fixed-window rate limiting (charter G5).

`RateLimiter` is a stdlib fixed-window counter keyed by an opaque string
(the app keys it by `request.client.host`), backed by a bounded
last-recently-used store guarded by a lock, with an injected clock so
tests never need to sleep.

This module never reads `X-Forwarded-For` or any other proxy header: a
client can set that header to whatever it likes, so trusting it would let
anyone pick their own rate-limit bucket. Instead the limiter relies on
`request.client.host`, which is the *peer* address as seen by the ASGI
server.

That matters for where this app actually runs. Charter G4 puts it behind
Caddy (with cloudflared in front) on `caddy_network`, so in production the
peer address uvicorn sees by default is Caddy's container IP for every
visitor -- not the visitor's own IP. Turning `request.client.host` back
into the real per-visitor address requires starting uvicorn with
`--proxy-headers` and `--forwarded-allow-ips` restricted to Caddy's
container(s) on `caddy_network`, so only that trusted proxy's
`X-Forwarded-For` is honored, and uvicorn (not this module) is the one
parsing it. That uvicorn flag change lives in the Dockerfile CMD, which is
outside this module's scope; see the task's follow-ups.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

ENV_VAR = "PENDEL_RATE_LIMIT_PER_MIN"
DEFAULT_LIMIT_PER_MIN = 60

_WINDOW_SECONDS = 60.0
_MAX_TRACKED_KEYS = 10_000


def rate_limit_per_min_from_env(env: dict[str, str] | None = None) -> int:
    """Read and validate `PENDEL_RATE_LIMIT_PER_MIN`, defaulting to 60.

    Raises ValueError if the variable is set but is not a positive
    integer, so startup fails loudly instead of silently disabling the
    limiter or applying a nonsensical limit.
    """
    raw = (os.environ if env is None else env).get(ENV_VAR)
    if raw is None:
        return DEFAULT_LIMIT_PER_MIN
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{ENV_VAR} must be a positive integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{ENV_VAR} must be a positive integer, got {raw!r}")
    return value


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int


class RateLimiter:
    """Fixed-window limiter: at most `limit_per_min` calls to `check()` per
    key in any rolling 60-second window starting from that key's first
    call in the window.

    The store is a `dict[key -> (window_start, count)]` bounded to
    `max_keys` entries via LRU eviction, so an unbounded stream of distinct
    keys (e.g. spoofed or churning IPs) cannot grow memory without limit.
    A lock protects the store since ASGI sync routes run in a threadpool.
    """

    def __init__(
        self,
        limit_per_min: int,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = _MAX_TRACKED_KEYS,
    ) -> None:
        if limit_per_min <= 0:
            raise ValueError("limit_per_min must be a positive integer")
        self._limit = limit_per_min
        self._clock = clock
        self._max_keys = max_keys
        self._lock = threading.Lock()
        self._windows: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def check(self, key: str) -> RateLimitResult:
        now = self._clock()
        with self._lock:
            window_start, count = self._windows.get(key, (now, 0))
            if now - window_start >= _WINDOW_SECONDS:
                window_start, count = now, 0
            count += 1
            self._windows[key] = (window_start, count)
            self._windows.move_to_end(key)
            if len(self._windows) > self._max_keys:
                self._windows.popitem(last=False)

            if count <= self._limit:
                return RateLimitResult(allowed=True, retry_after=0)
            retry_after = max(1, math.ceil(window_start + _WINDOW_SECONDS - now))
            return RateLimitResult(allowed=False, retry_after=retry_after)

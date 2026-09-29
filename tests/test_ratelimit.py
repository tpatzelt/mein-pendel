"""Tests for per-IP rate limiting (charter G5).

`RateLimiter` itself is tested directly with an injected clock (no real
sleeping). The FastAPI wiring -- lifespan building/tearing down
`app.state.rate_limiter`, the middleware's 429 + Retry-After, and the
`/healthz` / `/static/` exemptions -- is tested through the app with
`with TestClient(app) as c:`, since only the lifespan sets a limiter at
all; a bare `TestClient(app)` (as used by other test modules) never
triggers it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pendel.app import app
from pendel.ratelimit import ENV_VAR, RateLimiter


class FakeClock:
    """A monotonic-like clock a test can advance by hand."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def test_requests_up_to_the_limit_are_allowed() -> None:
    limiter = RateLimiter(limit_per_min=2, clock=FakeClock())

    first = limiter.check("1.1.1.1")
    second = limiter.check("1.1.1.1")

    assert first.allowed is True
    assert second.allowed is True


def test_request_past_the_limit_is_denied_with_retry_after() -> None:
    clock = FakeClock()
    limiter = RateLimiter(limit_per_min=2, clock=clock)
    limiter.check("1.1.1.1")
    limiter.check("1.1.1.1")

    third = limiter.check("1.1.1.1")

    assert third.allowed is False
    assert third.retry_after > 0


def test_a_different_key_is_unaffected() -> None:
    clock = FakeClock()
    limiter = RateLimiter(limit_per_min=1, clock=clock)
    limiter.check("1.1.1.1")

    other = limiter.check("2.2.2.2")

    assert other.allowed is True


def test_limit_resets_after_the_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(limit_per_min=1, clock=clock)
    limiter.check("1.1.1.1")
    assert limiter.check("1.1.1.1").allowed is False

    clock.advance(60.0)

    assert limiter.check("1.1.1.1").allowed is True


def test_rate_limit_per_min_from_env_defaults_to_60() -> None:
    from pendel.ratelimit import rate_limit_per_min_from_env

    assert rate_limit_per_min_from_env(env={}) == 60


@pytest.mark.parametrize("raw", ["abc", "0", "-1", "1.5"])
def test_rate_limit_per_min_from_env_rejects_invalid_values(raw: str) -> None:
    from pendel.ratelimit import rate_limit_per_min_from_env

    with pytest.raises(ValueError):
        rate_limit_per_min_from_env(env={ENV_VAR: raw})


def test_app_returns_429_with_retry_after_past_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "2")

    with TestClient(app) as c:
        first = c.get("/")
        second = c.get("/")
        third = c.get("/")

        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429
        assert "Retry-After" in third.headers

    assert app.state.rate_limiter is None


@pytest.mark.parametrize("raw", ["abc", "0"])
def test_app_startup_raises_on_invalid_env(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, raw)

    with pytest.raises(ValueError):
        with TestClient(app):
            pass


def test_healthz_is_never_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "1")

    with TestClient(app) as c:
        c.get("/")  # consume the only allowed request for "/"

        for _ in range(3):
            response = c.get("/healthz")
            assert response.status_code == 200


def test_static_assets_are_exempt_from_the_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "1")

    with TestClient(app) as c:
        c.get("/")  # consume the only allowed request for "/"

        for _ in range(3):
            response = c.get("/static/css/style.css")
            assert response.status_code == 200

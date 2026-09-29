from __future__ import annotations

import json
import socket
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "hafas"

_BLOCKED_FAMILIES = {socket.AF_INET, socket.AF_INET6}


class NetworkBlockedError(OSError):
    """Raised when a test attempts a real network connection."""


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any real TCP/UDP connection attempt; leave AF_UNIX alone for asyncio/anyio."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_connect(self: socket.socket, address: object) -> None:
        if self.family in _BLOCKED_FAMILIES:
            raise NetworkBlockedError(
                f"network access is blocked in tests: connect({address!r})"
            )
        real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: object) -> int:
        if self.family in _BLOCKED_FAMILIES:
            raise NetworkBlockedError(
                f"network access is blocked in tests: connect_ex({address!r})"
            )
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


@pytest.fixture
def hafas_replay() -> Callable[[dict[str, str]], httpx.MockTransport]:
    """Factory for an httpx.MockTransport that replays JSON fixtures by URL path."""

    def factory(routes: dict[str, str]) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path not in routes:
                raise LookupError(path)
            data = json.loads((FIXTURES_DIR / routes[path]).read_text())
            return httpx.Response(200, json=data)

        return httpx.MockTransport(handler)

    return factory

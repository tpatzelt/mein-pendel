from __future__ import annotations

import socket
from collections.abc import Callable

import httpx
import pytest

from conftest import NetworkBlockedError


def test_network_guard_blocks_real_tcp_connect() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkBlockedError, match="network access is blocked"):
            sock.connect(("127.0.0.1", 54321))


def test_network_guard_blocks_connect_ex() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(NetworkBlockedError, match="network access is blocked"):
            sock.connect_ex(("127.0.0.1", 54321))


def test_network_guard_leaves_af_unix_connect_alone(tmp_path) -> None:
    socket_path = str(tmp_path / "guard.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(socket_path)
        server.listen(1)
        client.connect(socket_path)
    finally:
        client.close()
        server.close()


def test_replay_returns_fixture_json(
    hafas_replay: Callable[[dict[str, str]], httpx.MockTransport],
) -> None:
    transport = hafas_replay({"/example": "harness/example.json"})
    with httpx.Client(transport=transport, base_url="http://hafas.test") as client:
        response = client.get("/example")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "id": "harness-example"}


def test_replay_unmapped_path_raises_lookup_error(
    hafas_replay: Callable[[dict[str, str]], httpx.MockTransport],
) -> None:
    transport = hafas_replay({"/example": "harness/example.json"})
    with httpx.Client(transport=transport, base_url="http://hafas.test") as client:
        with pytest.raises(LookupError, match="/missing"):
            client.get("/missing")

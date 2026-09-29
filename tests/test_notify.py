from __future__ import annotations

import json

import httpx
import pytest

from pendel.notify import Channel, ChannelSendError, FakeChannel, NtfyChannel, TelegramChannel

FAKE_TOKEN = "fake-token-for-tests"
FAKE_SERVER = "https://ntfy.example.invalid"


def _client(transport: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=transport)


def _recording_handler(response: httpx.Response):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return response

    return handler, calls


def test_channel_protocol_is_satisfied_by_fake_channel():
    channel: Channel = FakeChannel()
    channel.send("42", "hello")
    assert channel.sent == [("42", "hello")]


def test_fake_channel_records_multiple_sends_in_order():
    channel = FakeChannel()
    channel.send("a", "1")
    channel.send("b", "2")
    assert channel.sent == [("a", "1"), ("b", "2")]


# --- TelegramChannel ---------------------------------------------------------


def test_telegram_channel_requires_bot_token_or_env(monkeypatch):
    monkeypatch.delenv("PENDEL_TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValueError):
        TelegramChannel(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))


def test_telegram_channel_sends_bot_token_argument_via_url_and_json_body():
    handler, calls = _recording_handler(httpx.Response(200, json={"ok": True}))
    channel = TelegramChannel(_client(httpx.MockTransport(handler)), bot_token=FAKE_TOKEN)

    channel.send("12345", "disruption!")

    assert len(calls) == 1
    request = calls[0]
    assert request.url.path == f"/bot{FAKE_TOKEN}/sendMessage"
    assert request.url.host == "api.telegram.org"
    assert json.loads(request.content) == {"chat_id": "12345", "text": "disruption!"}


def test_telegram_channel_uses_token_from_env(monkeypatch):
    monkeypatch.setenv("PENDEL_TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    handler, calls = _recording_handler(httpx.Response(200, json={"ok": True}))
    channel = TelegramChannel(_client(httpx.MockTransport(handler)))

    channel.send("12345", "hi")

    assert FAKE_TOKEN in str(calls[0].url)


def test_telegram_channel_403_raises_sanitized_channel_send_error():
    handler, _calls = _recording_handler(httpx.Response(403, json={"ok": False}))
    channel = TelegramChannel(_client(httpx.MockTransport(handler)), bot_token=FAKE_TOKEN)

    with pytest.raises(ChannelSendError) as excinfo:
        channel.send("12345", "hi")

    exc = excinfo.value
    assert "403" in str(exc)
    assert FAKE_TOKEN not in str(exc)
    assert FAKE_TOKEN not in repr(exc)
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True


def test_telegram_channel_transport_error_is_wrapped_without_leaking_url():
    leaky_url = f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection refused: {leaky_url}", request=request)

    channel = TelegramChannel(_client(httpx.MockTransport(handler)), bot_token=FAKE_TOKEN)

    with pytest.raises(ChannelSendError) as excinfo:
        channel.send("12345", "hi")

    exc = excinfo.value
    assert FAKE_TOKEN not in str(exc)
    assert leaky_url not in str(exc)
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True


# --- NtfyChannel --------------------------------------------------------------


def test_ntfy_channel_requires_server_or_env(monkeypatch):
    monkeypatch.delenv("PENDEL_NTFY_URL", raising=False)
    with pytest.raises(ValueError):
        NtfyChannel(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))


def test_ntfy_channel_sends_topic_and_text_body():
    handler, calls = _recording_handler(httpx.Response(200))
    channel = NtfyChannel(_client(httpx.MockTransport(handler)), server=FAKE_SERVER)

    channel.send("my-topic", "disruption!")

    assert len(calls) == 1
    request = calls[0]
    assert str(request.url) == f"{FAKE_SERVER}/my-topic"
    assert request.content == b"disruption!"


def test_ntfy_channel_uses_server_from_env(monkeypatch):
    monkeypatch.setenv("PENDEL_NTFY_URL", FAKE_SERVER)
    handler, calls = _recording_handler(httpx.Response(200))
    channel = NtfyChannel(_client(httpx.MockTransport(handler)))

    channel.send("my-topic", "hi")

    assert str(calls[0].url).startswith(FAKE_SERVER)


@pytest.mark.parametrize(
    "topic",
    ["has/slash", "has?query", "has#fragment", "", "trailing-newline\n", "x" * 65],
)
def test_ntfy_channel_rejects_invalid_topic_before_any_request(topic):
    handler, calls = _recording_handler(httpx.Response(200))
    channel = NtfyChannel(_client(httpx.MockTransport(handler)), server=FAKE_SERVER)

    with pytest.raises(ValueError):
        channel.send(topic, "hi")

    assert calls == []


def test_ntfy_channel_403_raises_sanitized_channel_send_error():
    handler, _calls = _recording_handler(httpx.Response(403))
    channel = NtfyChannel(_client(httpx.MockTransport(handler)), server=FAKE_SERVER)

    with pytest.raises(ChannelSendError) as excinfo:
        channel.send("my-topic", "hi")

    exc = excinfo.value
    assert "403" in str(exc)
    assert FAKE_SERVER not in str(exc)
    assert FAKE_SERVER not in repr(exc)
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True


def test_ntfy_channel_transport_error_is_wrapped_without_leaking_url():
    leaky_url = f"{FAKE_SERVER}/my-topic"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection refused: {leaky_url}", request=request)

    channel = NtfyChannel(_client(httpx.MockTransport(handler)), server=FAKE_SERVER)

    with pytest.raises(ChannelSendError) as excinfo:
        channel.send("my-topic", "hi")

    exc = excinfo.value
    assert FAKE_SERVER not in str(exc)
    assert leaky_url not in str(exc)
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True

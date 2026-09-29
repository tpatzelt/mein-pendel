"""Notification channel interface (charter goal G3): Telegram and ntfy
adapters plus a FakeChannel for tests. No real message is ever sent
outside this package; every adapter takes an injected httpx.Client."""

from pendel.notify.channel import (
    Channel,
    ChannelSendError,
    FakeChannel,
    NtfyChannel,
    TelegramChannel,
)

__all__ = [
    "Channel",
    "ChannelSendError",
    "FakeChannel",
    "NtfyChannel",
    "TelegramChannel",
]

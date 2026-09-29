"""Scheduler process entrypoint (charter G3): `python -m pendel.runner` wires
up a real `db` connection, a real `HafasClient` and the real notification
channels from environment variables, then either runs one `Scheduler.tick`
(with `--once`) or `Scheduler.run_forever`.

Environment variables read (directly here, or by the modules this builds --
G4's `.pendel.env.example` lists all of these):

- `PENDEL_DATA_DIR`: sqlite data directory (see `pendel.db.data_dir`).
- `PENDEL_HAFAS_BASE_URL`: HAFAS REST base URL, defaults to
  `https://v6.bvg.transport.rest` (see `pendel.hafas.HafasClient`).
- `PENDEL_TELEGRAM_BOT_TOKEN`: Telegram bot token. When unset, the
  `'telegram'` channel built here is a no-op that logs and sends nothing
  instead of raising `ValueError`.
- `PENDEL_NTFY_URL`: ntfy server base URL. When unset, the `'ntfy'` channel
  built here is a no-op that logs and sends nothing instead of raising
  `ValueError`.
- `PENDEL_CHECK_LEAD_MIN`: minutes before a commute's departure window that
  it becomes due (see `pendel.scheduler.Scheduler`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
from collections.abc import Mapping

import httpx

from pendel import db
from pendel.commute import BERLIN
from pendel.hafas import HafasClient
from pendel.notify import Channel, NtfyChannel, TelegramChannel
from pendel.scheduler import Scheduler

logger = logging.getLogger(__name__)


class _NoopChannel:
    """Channel for a kind with no configured credentials: logs and sends
    nothing, so `tracker._send_to_all`'s `channels[kind]` lookup never
    raises `KeyError` for a channel row whose kind is not configured."""

    def __init__(self, kind: str) -> None:
        self._kind = kind

    def send(self, target: str, text: str) -> None:
        logger.warning(
            "dropping notification: %s channel is not configured", self._kind
        )


def build_channels(
    http_client: httpx.Client, env: Mapping[str, str]
) -> dict[str, Channel]:
    """Build the real channel dict: `'telegram'` only if
    `PENDEL_TELEGRAM_BOT_TOKEN` is set, `'ntfy'` only if `PENDEL_NTFY_URL` is
    set. The other kind maps to a `_NoopChannel` so an unconfigured kind
    never raises."""
    channels: dict[str, Channel] = {}

    bot_token = env.get("PENDEL_TELEGRAM_BOT_TOKEN")
    channels["telegram"] = (
        TelegramChannel(http_client, bot_token=bot_token)
        if bot_token
        else _NoopChannel("telegram")
    )

    ntfy_url = env.get("PENDEL_NTFY_URL")
    channels["ntfy"] = (
        NtfyChannel(http_client, server=ntfy_url) if ntfy_url else _NoopChannel("ntfy")
    )

    return channels


def _build_http_client() -> httpx.Client:
    """Factory for the shared `httpx.Client`, split out so tests can
    monkeypatch it to inject an `httpx.MockTransport`."""
    return httpx.Client(timeout=10.0)


def _clock() -> dt.datetime:
    """Wall clock in Europe/Berlin, split out so tests can monkeypatch a
    fixed instant instead of depending on real time."""
    return dt.datetime.now(BERLIN)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m pendel.runner")
    parser.add_argument(
        "--once",
        action="store_true",
        help="run exactly one Scheduler.tick and exit, instead of run_forever",
    )
    args = parser.parse_args(argv)

    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO)

    conn = db.connect()
    http_client = _build_http_client()
    hafas_client = HafasClient(http_client)
    channels = build_channels(http_client, os.environ)
    scheduler = Scheduler(conn, hafas_client, channels, clock=_clock)

    if args.once:
        scheduler.tick(_clock())
    else:
        scheduler.run_forever()


if __name__ == "__main__":
    main()

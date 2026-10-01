# Mein Pendel

Save your Berlin commute, get a message only when something on it is disrupted
(Ersatzverkehr, cancellations, construction, long delays).

Built by a NIGHTSHIFT run — see `docs/CHARTER.md` for the goals.

```bash
uv sync
uv run pytest -q
```

## Run locally

Each process is started with `uv run` from the repo root. `PENDEL_DATA_DIR`
must be set for any of the three to read or write the database — the web
app itself starts and serves `/healthz` without it, but any route that opens
the database raises `KeyError` if it is unset, and the scheduler and poller
both open the database on startup.

```bash
export PENDEL_DATA_DIR=./data

# the web app
uv run uvicorn pendel.app:app

# the scheduler that checks due commutes and sends notifications
uv run python -m pendel.runner        # add --once to run a single tick and exit

# the Telegram getUpdates poller that links /start deep-link tokens
uv run python -m pendel.telegram_poll
```

`pendel.telegram_poll` logs that it is disabled and exits immediately if
`PENDEL_TELEGRAM_BOT_TOKEN` is unset, without touching the database.

## Using Mein Pendel

Setup takes at most three screens and works without JavaScript. On `/stops`,
search the origin and destination by name — no stop ID to type. On
`/commutes/new`, tick the lines that actually depart from the origin;
defaults are Mon–Fri, a 30-minute window starting at the next full half
hour, and a 5-minute delay threshold.

`/commutes` lists every saved commute, each with edit, pause/resume and
delete; a departure window may cross midnight (e.g. 23:30–00:30).

`/today` shows one card per commute: status OK, disrupted, paused or
checking failed, the next departures with planned vs. real-time time and
platform, and the alternative when there is one.

`/notifications` lets you link a Telegram or ntfy channel, unlink each one
individually, and send a test message to check it works.

## Configuration

All configuration comes from environment variables; `deploy/.pendel.env.example`
is the canonical list with deployment-specific comments. Here is what each one
means to the code:

| Variable | Meaning |
| --- | --- |
| `PENDEL_DATA_DIR` | Directory holding `pendel.db`. Required, no default: any code path that opens the database raises `KeyError` if it is unset. |
| `PENDEL_CHECK_LEAD_MIN` | Minutes before a saved commute's departure window that it becomes due for a check. Must be a positive integer; defaults to `30`. |
| `PENDEL_HAFAS_BASE_URL` | Base URL of the HAFAS REST API. Defaults to `https://v6.bvg.transport.rest`. |
| `PENDEL_PUBLIC_URL` | Public origin of the web app (e.g. `https://pendel.example.org`). Notifications link to its `/today` page; when unset they carry only the bare path `/today`, which is not a link in Telegram or ntfy. |
| `PENDEL_NTFY_URL` | ntfy server base URL. When unset, the ntfy channel is a no-op that logs and sends nothing instead of raising. |
| `PENDEL_RATE_LIMIT_PER_MIN` | Per-IP request limit enforced by the web app's rate limiter. Must be a positive integer; defaults to `60`; past the limit a request gets `429` with `Retry-After`. |
| `PENDEL_TELEGRAM_BOT_TOKEN` | Telegram bot token. When unset, the Telegram channel is a no-op that logs and sends nothing, and `pendel.telegram_poll` logs that it is disabled and exits instead of polling. |
| `PENDEL_TELEGRAM_BOT_USERNAME` | Telegram bot username used to build the `/start` deep link shown on the notification settings page. |
| `FORWARDED_ALLOW_IPS` | Read by uvicorn itself (not application code) to trust `X-Forwarded-For` from Caddy's container(s) only, so the per-IP rate limiter sees real visitor IPs instead of Caddy's. |

## Tests

`uv run pytest -q` is fully offline: HTTP is replayed from fixtures under
`tests/fixtures/hafas/**`, and a test that touches the network is a failing
test. The engine fixtures under `tests/fixtures/hafas/engine/**` are
synthetic (file names start with `synthetic_`), not recordings of real
disruptions.

## Deployment

See `DEPLOY.md` for the Dockerfile, `deploy/compose.yaml`, the exact Caddy
route and cloudflared ingress lines, and the full deploy steps.

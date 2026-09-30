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

# G2: the web app (stop search, saved commutes, "today on my route")
uv run uvicorn pendel.app:app

# G3: the scheduler that checks due commutes and sends notifications
uv run python -m pendel.runner        # add --once to run a single tick and exit

# G3: the Telegram getUpdates poller that links /start deep-link tokens
uv run python -m pendel.telegram_poll
```

`pendel.telegram_poll` logs that it is disabled and exits immediately if
`PENDEL_TELEGRAM_BOT_TOKEN` is unset, without touching the database.

## Configuration

All configuration comes from environment variables; `deploy/.pendel.env.example`
is the canonical list with deployment-specific comments. Here is what each one
means to the code:

| Variable | Meaning |
| --- | --- |
| `PENDEL_DATA_DIR` | Directory holding `pendel.db`. Required, no default: any code path that opens the database raises `KeyError` if it is unset. |
| `PENDEL_CHECK_LEAD_MIN` | Minutes before a saved commute's departure window that it becomes due for a check. Must be a positive integer; defaults to `30`. |
| `PENDEL_HAFAS_BASE_URL` | Base URL of the HAFAS REST API. Defaults to `https://v6.bvg.transport.rest`. |
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
route and cloudflared ingress lines, and the full deploy steps (charter G4).

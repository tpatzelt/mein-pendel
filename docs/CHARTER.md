# Mein Pendel

Charter  (immutable — agents must not edit)

## Goals
- G1: A commute model and disruption engine. A user's commute is a saved route (origin
  stop, destination stop, the lines they ride, weekdays, departure window). Given the
  current departures/trip data and remarks from the VBB/BVG HAFAS REST API
  (v6.bvg.transport.rest), the engine decides deterministically whether that commute is
  affected (cancellation, replacement bus/Ersatzverkehr, delay above a threshold,
  construction remark on a used line/stop) and produces a short German+English reason
  plus a suggested alternative connection when one exists.
- G2: A small, fast web app (FastAPI + server-rendered HTML, no JS build step) where a
  visitor searches stops, saves one or more commutes without creating a password
  account, and sees "today on my route" on one page. Mobile-first, works at 360px width,
  German and English UI.
- G3: Notifications. A scheduler checks each saved commute shortly before its departure
  window and notifies only when G1 says it is affected and only once per disruption.
  Channels: Telegram bot (link via /start deep-link token) and ntfy topic. The channel
  layer is an interface with fakes in tests; no real message is ever sent during the run.
- G4: Deployable in Tim's homelab. A Dockerfile (non-root, healthcheck endpoint), a
  `deploy/compose.yaml` + `deploy/.pendel.env.example` following the homelab conventions
  (external `caddy_network`, data at `/opt/dockerdata/pendel`, pinned base images,
  `env_file` secrets, no host ports), a GitHub Actions workflow that runs tests and
  publishes `ghcr.io/tpatzelt/mein-pendel:latest` + `sha-<short>`, and a DEPLOY.md with
  the exact Caddy route and cloudflared ingress lines to add.
- G5: Launch-ready for a Reddit post: an Impressum and a Datenschutzerklärung page with
  placeholders for Tim's details, a privacy-minimal data model (no email, no name; delete
  my data in one click), per-IP rate limiting, and an About page that says it is free and
  links an optional Ko-fi placeholder.

## Non-goals
- No native mobile app, no Web Push, no SMS.
- No user accounts with passwords, no payments, no ads, no analytics trackers.
- No scraping of bvg.de, s-bahn.berlin or any site; only the HAFAS REST API.
- No edits to the homelab repo; G4 produces files inside this repo only.
- No regional/national (DB Fernverkehr) routing beyond what the VBB API returns.
- No reformatting or restructuring not required by a specific task.

## Constraints
- Python >= 3.12, uv: `uv sync`, `uv run pytest -q`. Dependencies are allowed but must be
  justified in the commit message; prefer stdlib + fastapi + httpx + jinja2 + sqlite.
- SQLite at a path from `PENDEL_DATA_DIR`; schema migrations are plain SQL files.
- `uv run pytest -q` must stay green and fully offline: HTTP is replayed from fixtures
  under `tests/fixtures/**`; a test that touches the network is a failing test.
- The sandbox can reach v6.bvg.transport.rest. Use it to learn real response shapes and
  record fixtures (Ersatzverkehr, cancellations, remarks, S-Bahn Ring works). Keep live
  calls small and never loop them; the public instance is rate-limited (~100 req/min),
  and the production code must cache and back off accordingly.
- Telegram and ntfy are unreachable from the sandbox and must stay faked in tests.
- No secrets, tokens, real domains, LAN IPs or personal data in the repository;
  configuration comes from environment variables documented in `.pendel.env.example`.
- Times are Europe/Berlin throughout; DST transitions are tested.

## Definition of done per goal
- G1: Given recorded fixtures for at least five real disruption kinds plus an undisturbed
  day, the engine returns the expected affected/unaffected verdict and reason for each,
  covered by table-driven tests; a false-positive test proves a disruption on a line the
  user does not ride is ignored.
- G2: `uv run uvicorn pendel.app:app` serves stop search, save commute, "today on my
  route" and delete-my-data; each route has a test via FastAPI's TestClient; pages pass
  an HTML-structure test for the 360px layout (viewport meta, no fixed widths).
- G3: A test drives the scheduler across a simulated morning and asserts exactly one
  notification per disruption per commute, none when unaffected, and a resolved message
  when a disruption clears; the Telegram linking flow is tested end-to-end against a fake.
- G4: `docker build .` succeeds and the container answers its healthcheck in a test or
  script run inside the sandbox; `docker compose -f deploy/compose.yaml config` validates
  with the example env; the workflow file is valid YAML; DEPLOY.md lists every step.
- G5: The legal pages exist and are linked in the footer; delete-my-data removes every row
  for that user (tested); rate limiting returns 429 past the limit (tested).

## Priority order
G1 > G3 > G2 > G4 > G5

## Allowed idle work (when the backlog is empty)
Tests, fixtures of further real disruption kinds, documentation, accessibility and
small refactors strictly within G1–G5. Nothing new.

## Projects
- repo: mein-pendel   goals: [G1, G2, G3, G4, G5]   test_cmd: "uv run pytest -q"

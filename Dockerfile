# syntax=docker/dockerfile:1

# uv, pinned by a version tag (not a digest).
FROM ghcr.io/astral-sh/uv:0.12.17 AS uv

FROM python:3.12.7-slim-bookworm

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PENDEL_DATA_DIR=/data

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# --locked fails the build if uv.lock is stale instead of silently
# re-resolving; --no-dev keeps pytest and other dev-only deps out of the
# runtime image.
RUN uv sync --locked --no-dev

RUN groupadd --gid 10001 pendel \
    && useradd --uid 10001 --gid pendel --system --home-dir /app --shell /usr/sbin/nologin pendel \
    && mkdir -p /data \
    && chown -R pendel:pendel /app /data

USER pendel

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD /app/.venv/bin/python -c "import urllib.request, sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"

# Calls the venv binary directly rather than `uv run`, which would try to
# re-sync the environment (including dev deps) on every container start.
CMD ["/app/.venv/bin/uvicorn", "pendel.app:app", "--host", "0.0.0.0", "--port", "8000"]

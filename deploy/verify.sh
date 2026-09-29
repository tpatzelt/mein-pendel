#!/usr/bin/env bash
# G4's deploy verification, run by CI (and by a human) before publishing.
# Never contacts Telegram, ntfy or HAFAS; the only network request is the
# image's own /healthz, and only from inside the sandboxed container.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGE_TAG="mein-pendel:verify"
CONTAINER_NAME="mein-pendel-verify-$$"
TMPDIR="$(mktemp -d)"

cleanup() {
  local status=$?
  set +e
  if docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1
  fi
  rm -rf "$TMPDIR"
  exit "$status"
}
trap cleanup EXIT

echo "==> deploy/verify.sh: validating deploy/compose.yaml with the example env"
cp "$ROOT/deploy/compose.yaml" "$TMPDIR/compose.yaml"
cp "$ROOT/deploy/.pendel.env.example" "$TMPDIR/.pendel.env"
docker compose -f "$TMPDIR/compose.yaml" config -q

echo "==> deploy/verify.sh: parsing ci.yaml and compose.yaml as YAML"
uv run --no-project --with pyyaml python -c "
import sys, yaml
for path in sys.argv[1:]:
    with open(path) as handle:
        yaml.safe_load(handle)
    print(f'{path}: valid YAML')
" "$ROOT/.github/workflows/ci.yaml" "$ROOT/deploy/compose.yaml"

echo "==> deploy/verify.sh: building the image"
docker build -t "$IMAGE_TAG" "$ROOT"

echo "==> deploy/verify.sh: starting the web container"
docker run -d \
  --name "$CONTAINER_NAME" \
  -e PENDEL_DATA_DIR=/data \
  -e PENDEL_TELEGRAM_BOT_TOKEN= \
  -e PENDEL_TELEGRAM_BOT_USERNAME= \
  -e PENDEL_NTFY_URL= \
  "$IMAGE_TAG" >/dev/null

echo "==> deploy/verify.sh: waiting for the container to report healthy"
interval=3
max_wait=90
elapsed=0
status="starting"
while [ "$elapsed" -lt "$max_wait" ]; do
  if status="$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null)"; then
    :
  else
    status="starting"
  fi
  if [ "$status" = "healthy" ]; then
    break
  fi
  sleep "$interval"
  elapsed=$((elapsed + interval))
done

if [ "$status" != "healthy" ]; then
  echo "deploy/verify.sh: container did not become healthy within ${max_wait}s (last status: $status)" >&2
  docker logs "$CONTAINER_NAME" >&2
  exit 1
fi

echo "deploy/verify.sh: all checks passed"

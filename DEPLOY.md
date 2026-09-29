# Deploying Mein Pendel (charter G4)

These steps deploy Mein Pendel into Tim's homelab, behind Caddy and
cloudflared. The homelab repo itself is not edited by these steps -- this
repo produces every file referenced below, and only the two snippets in
steps 6 and 7 are added to the homelab repo by hand.

`docker build .` and `docker compose -f deploy/compose.yaml config` could
not be run in the NIGHTSHIFT sandbox, because its guard blocks every Docker
call. Both must be run by a human before relying on this deployment.

## Steps

1. On the target host, pull or build the image so
   `ghcr.io/tpatzelt/mein-pendel:latest` (or a `sha-<short>` tag from CI) is
   available locally:

   ```
   docker pull ghcr.io/tpatzelt/mein-pendel:latest
   ```

2. Copy the env example to `deploy/.pendel.env`, next to `compose.yaml` --
   that is where `env_file: .pendel.env` resolves -- and edit it:

   ```
   cp deploy/.pendel.env.example deploy/.pendel.env
   $EDITOR deploy/.pendel.env
   ```

   Uncomment and fill in only the channels you want to enable
   (`PENDEL_TELEGRAM_BOT_TOKEN` + `PENDEL_TELEGRAM_BOT_USERNAME`, and/or
   `PENDEL_NTFY_URL`). The `telegram` compose service only does anything
   once `PENDEL_TELEGRAM_BOT_TOKEN` is set; leave it commented out and that
   service simply exits 0 without polling Telegram. Leave
   `PENDEL_CHECK_LEAD_MIN` and `PENDEL_RATE_LIMIT_PER_MIN` commented out to
   use their built-in defaults, or uncomment them with a real positive
   integer. `deploy/.pendel.env` is gitignored and must never be committed
   or placed inside `/opt/dockerdata/pendel`.

3. Look up the `caddy_network` subnet and set `FORWARDED_ALLOW_IPS` to it
   in `deploy/.pendel.env`, so uvicorn trusts `X-Forwarded-For` from Caddy
   alone and the per-IP rate limiter (G5) sees real visitor IPs instead of
   Caddy's container IP for every request:

   ```
   docker network inspect caddy_network --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
   ```

   Uncomment `FORWARDED_ALLOW_IPS` in `deploy/.pendel.env` and set it to
   that subnet (a CIDR range, e.g. the output above). Never set it to `*`:
   any other container on `caddy_network` could then spoof
   `X-Forwarded-For`.

4. Create the data directory and give it to the container's user (UID/GID
   10001, created in the Dockerfile):

   ```
   sudo mkdir -p /opt/dockerdata/pendel
   sudo chown -R 10001:10001 /opt/dockerdata/pendel
   ```

5. Start all three services (`web`, `scheduler`, `telegram`):

   ```
   docker compose -f deploy/compose.yaml up -d
   ```

6. Add a Caddy route so `https://pendel.example.org` (replace with the real
   subdomain) reverse-proxies to the `web` service on the `caddy_network`:

   ```
   pendel.example.org {
       reverse_proxy web:8000
   }
   ```

7. Add a cloudflared ingress rule pointing the same hostname at Caddy
   (replace the placeholder hostname; `service` matches whatever hostname
   and port the homelab's Caddy listens on):

   ```
   ingress:
     - hostname: pendel.example.org
       service: http://caddy:80
   ```

8. Confirm all three containers are healthy:

   ```
   docker compose -f deploy/compose.yaml ps
   ```

   `web` should report `healthy` (it polls its own `/healthz`); `scheduler`
   and `telegram` both have their healthcheck disabled, since neither
   serves HTTP, so they only show as `running` (or `restarting`/exited for
   `telegram` if `PENDEL_TELEGRAM_BOT_TOKEN` is unset, since it then exits
   0 immediately).

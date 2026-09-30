# Deploying Mein Pendel (charter G4)

These steps deploy Mein Pendel into Tim's homelab, behind Caddy and
cloudflared. The homelab repo itself is not edited by these steps -- this
repo produces every file referenced below, and only the two snippets in
steps 7 and 8 are added to the homelab repo by hand.

`docker build .` and `docker compose -f deploy/compose.yaml config` are
checked by `deploy/verify.sh`, which CI runs on every push. The NIGHTSHIFT
sandbox cannot run it itself, because its guard blocks every Docker call --
a human can run `bash deploy/verify.sh` locally at any time to repeat the
same checks CI does.

## Steps

1. Publish the code so CI can build the image. The repo has no GitHub
   remote yet; the workflow only runs on a push to `main`:

   ```
   gh repo create tpatzelt/mein-pendel --public --source . --push
   ```

   After the first green run, GHCR creates `ghcr.io/tpatzelt/mein-pendel`
   as a **private** package. Either make it public under the package's
   settings on GitHub, or run `docker login ghcr.io` on the host with a
   token that has `read:packages` before step 2.

2. On the target host, pull or build the image so
   `ghcr.io/tpatzelt/mein-pendel:latest` (or a `sha-<short>` tag from CI) is
   available locally:

   ```
   docker pull ghcr.io/tpatzelt/mein-pendel:latest
   ```

3. Copy the env example to `deploy/.pendel.env`, next to `compose.yaml` --
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

4. Look up the `caddy_network` subnet and set `FORWARDED_ALLOW_IPS` to it
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

5. Create the data directory and give it to the container's user (UID/GID
   10001, created in the Dockerfile):

   ```
   sudo mkdir -p /opt/dockerdata/pendel
   sudo chown -R 10001:10001 /opt/dockerdata/pendel
   ```

6. Start all three services (`web`, `scheduler`, `telegram`):

   ```
   docker compose -f deploy/compose.yaml up -d
   ```

7. Add a Caddy route inside the homelab Caddyfile's public `*.{$DOMAIN}`
   block (next to `@jonas`), so `pendel.<domain>` reverse-proxies to the
   `pendel-web` container on `caddy_network`:

   ```
   	@pendel host pendel.{$DOMAIN}
   	handle @pendel {
   		reverse_proxy pendel-web:8000
   	}
   ```

   Then reload Caddy (`docker exec caddy caddy reload --config
   /etc/caddy/Caddyfile`).

8. Add a cloudflared ingress rule for the same hostname, above the final
   `http_status:404` catch-all, in the same shape as the existing entries
   (Caddy terminates TLS, so cloudflared talks HTTPS to it):

   ```
     - hostname: pendel.<domain>
       service: https://caddy:443
       originRequest:
         originServerName: pendel.<domain>
   ```

   and add the public hostname to the tunnel in the Cloudflare dashboard
   (or `cloudflared tunnel route dns <tunnel> pendel.<domain>`).

9. Confirm all three containers are healthy:

   ```
   docker compose -f deploy/compose.yaml ps
   ```

   `web` should report `healthy` (it polls its own `/healthz`); `scheduler`
   and `telegram` both have their healthcheck disabled, since neither
   serves HTTP, so they only show as `running` (or `restarting`/exited for
   `telegram` if `PENDEL_TELEGRAM_BOT_TOKEN` is unset, since it then exits
   0 immediately).

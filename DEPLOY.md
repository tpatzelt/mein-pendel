# Deploying Mein Pendel (charter G4)

These steps deploy Mein Pendel into Tim's homelab, behind Caddy and
cloudflared. The homelab repo itself is not edited by these steps -- this
repo produces every file referenced below, and only the two snippets in
steps 5 and 6 are added to the homelab repo by hand.

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
   `PENDEL_NTFY_URL`). Leave `PENDEL_CHECK_LEAD_MIN` and
   `PENDEL_RATE_LIMIT_PER_MIN` commented out to use their built-in defaults,
   or uncomment them with a real positive integer. `deploy/.pendel.env` is
   gitignored and must never be committed or placed inside
   `/opt/dockerdata/pendel`.

3. Create the data directory and give it to the container's user (UID/GID
   10001, created in the Dockerfile):

   ```
   sudo mkdir -p /opt/dockerdata/pendel
   sudo chown -R 10001:10001 /opt/dockerdata/pendel
   ```

4. Start both services:

   ```
   docker compose -f deploy/compose.yaml up -d
   ```

5. Add a Caddy route so `https://pendel.example.org` (replace with the real
   subdomain) reverse-proxies to the `web` service on the `caddy_network`:

   ```
   pendel.example.org {
       reverse_proxy web:8000
   }
   ```

6. Add a cloudflared ingress rule pointing the same hostname at Caddy
   (replace the placeholder hostname; `service` matches whatever hostname
   and port the homelab's Caddy listens on):

   ```
   ingress:
     - hostname: pendel.example.org
       service: http://caddy:80
   ```

7. Confirm both containers are healthy:

   ```
   docker compose -f deploy/compose.yaml ps
   ```

   `web` should report `healthy` (it polls its own `/healthz`); `scheduler`
   has its healthcheck disabled, since `pendel.runner` serves no HTTP, so it
   only shows as `running`.

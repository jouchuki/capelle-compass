#!/usr/bin/env bash
# =============================================================================
# scripts/spawn-yuta.sh
# -----------------------------------------------------------------------------
# Purpose:
#   Clone the production yuta container (`yuta-okkotsu`) into a new instance
#   named `yuta-<given>-<family>` (e.g. `yuta-maki-zenin`), point it at the
#   shared Postgres + RabbitMQ + ChromaDB, join the tailnet, wait for it to
#   come up, and then register it with the nginx load balancer.
#
# Usage:
#   ./spawn-yuta.sh <given>-<family>
#
#   Example:
#     ./spawn-yuta.sh maki-zenin
#   produces container `yuta-maki-zenin`.
#
# Strategy (why lxc copy and not a fresh deploy-lxc.sh):
#   yuta-okkotsu is the gold image: it already has the backend venv, the
#   frontend build, the ohrs binary, and the ChromaDB + CBS catalog laid
#   down. `lxc copy` clones all of that in seconds and is idempotent with
#   respect to code version across yutas (every clone at time T is
#   byte-identical). If you want a fresh install instead, run
#   scripts/deploy-lxc.sh yuta-<slug> — but that means every yuta can
#   drift from the template if deploy-lxc.sh is edited between spawns.
#
# Runs on:
#   The workstation. SSHes into `gojo` for lxc operations and into
#   `masamichi-yaga` (via tailnet) for the nginx reload.
#
# Required env (fails fast if any are unset — no partial spawns):
#   CAPELLE_JWT_SECRET           Shared JWT secret (must match across all yutas).
#   CAPELLE_POSTGRES_DSN         postgres://capelle:<pw>@aoi-todo:5432/capelle
#   CAPELLE_RABBITMQ_URL         amqp://capelle:<pw>@kiyotaka-ijichi:5672/capelle
#   CAPELLE_CHROMA_HTTP          e.g. http://chroma-host:8000 (if externalised)
#   CAPELLE_ADMIN_EMAILS         Comma-separated list.
#   CAPELLE_SUPPORT_EMAIL        Contact address shown in the UI.
#   OPENAI_API_KEY               OpenAI key (for the LLM calls).
#   TAILSCALE_AUTHKEY            Reusable Tailscale key (container joins tailnet).
#
# Optional env:
#   CAPELLE_PUBLIC_ORIGIN        Default: https://data-compass.org
#   LB_CONTAINER                 Default: masamichi-yaga
#   TEMPLATE_CONTAINER           Default: yuta-okkotsu
#   HOLD_FROM_LB                 If "1", skip the final nginx-upstream append
#                                step. The container is still spawned, joined
#                                to the tailnet, and healthy — it is simply
#                                not yet serving public traffic. Use this when
#                                the new yuta must be brought up pre-code-cutover
#                                (e.g. v1.1 where both yutas need the new code
#                                before we let the LB route to either).
#
# Idempotency:
#   Refuses to spawn if the target container already exists.
#   Appending to the nginx upstream block is guarded by a grep so re-running
#   after a partial failure does not produce duplicate `server` lines.
# =============================================================================

set -euo pipefail

TAG="[spawn-yuta]"
HOST="gojo"
TEMPLATE_CONTAINER="${TEMPLATE_CONTAINER:-yuta-okkotsu}"
LB_CONTAINER="${LB_CONTAINER:-masamichi-yaga}"
CAPELLE_PUBLIC_ORIGIN="${CAPELLE_PUBLIC_ORIGIN:-https://data-compass.org}"

# ---- arg validation --------------------------------------------------------
if [[ $# -ne 1 ]]; then
    echo "$TAG usage: $0 <given>-<family>  (e.g. $0 maki-zenin)" >&2
    exit 2
fi

SLUG="$1"
if [[ ! "$SLUG" =~ ^[a-z0-9-]+$ ]]; then
    echo "$TAG FATAL: slug '${SLUG}' must match ^[a-z0-9-]+\$" >&2
    exit 2
fi
if [[ "$SLUG" == "okkotsu" ]]; then
    echo "$TAG FATAL: refuse to overwrite the template (slug 'okkotsu')" >&2
    exit 2
fi

NEW_CONTAINER="yuta-${SLUG}"

# ---- env checks ------------------------------------------------------------
: "${CAPELLE_JWT_SECRET:?$TAG CAPELLE_JWT_SECRET is required}"
: "${CAPELLE_POSTGRES_DSN:?$TAG CAPELLE_POSTGRES_DSN is required}"
: "${CAPELLE_RABBITMQ_URL:?$TAG CAPELLE_RABBITMQ_URL is required}"
: "${CAPELLE_CHROMA_HTTP:?$TAG CAPELLE_CHROMA_HTTP is required}"
: "${CAPELLE_ADMIN_EMAILS:?$TAG CAPELLE_ADMIN_EMAILS is required}"
: "${CAPELLE_SUPPORT_EMAIL:?$TAG CAPELLE_SUPPORT_EMAIL is required}"
# OPENAI_API_KEY is used when the project falls back to direct provider
# access; the production deploy runs on Codex OAuth (CODEX_* vars), so both
# sets are accepted and only one is required.
: "${OPENAI_API_KEY:=}"
: "${CODEX_ACCESS_TOKEN:=}"
: "${CODEX_REFRESH_TOKEN:=}"
: "${CODEX_BASE_URL:=https://chatgpt.com/backend-api/codex}"
if [[ -z "$OPENAI_API_KEY" && -z "$CODEX_ACCESS_TOKEN" ]]; then
    echo "$TAG neither OPENAI_API_KEY nor CODEX_ACCESS_TOKEN is set" >&2
    exit 1
fi
: "${CAPELLE_STORE:=sqlite}"
: "${CAPELLE_QUEUE:=memory}"
: "${CAPELLE_WS_HUB:=memory}"
: "${CAPELLE_TRUSTED_PROXIES:=}"
: "${TAILSCALE_AUTHKEY:?$TAG TAILSCALE_AUTHKEY is required}"

echo "$TAG === Spawning ${NEW_CONTAINER} from ${TEMPLATE_CONTAINER} ==="

# ---- 1. refuse to spawn over an existing container -----------------------
if ssh "$HOST" "lxc info ${NEW_CONTAINER} >/dev/null 2>&1"; then
    echo "$TAG FATAL: container ${NEW_CONTAINER} already exists on ${HOST}" >&2
    exit 1
fi
if ! ssh "$HOST" "lxc info ${TEMPLATE_CONTAINER} >/dev/null 2>&1"; then
    echo "$TAG FATAL: template ${TEMPLATE_CONTAINER} does not exist on ${HOST}" >&2
    exit 1
fi

# ---- 2. lxc copy (template must be stopped for a consistent copy) --------
echo "$TAG [1/7] lxc copy ${TEMPLATE_CONTAINER} -> ${NEW_CONTAINER}..."
# Stopping the template is the safest way to avoid a half-written sqlite or
# in-progress write on the venv. If the template is the live prod, arrange
# for brief downtime (seconds) or snapshot first — left to the operator.
ssh "$HOST" "lxc stop ${TEMPLATE_CONTAINER}"
ssh "$HOST" "lxc copy ${TEMPLATE_CONTAINER} ${NEW_CONTAINER}"
ssh "$HOST" "lxc start ${TEMPLATE_CONTAINER}"
ssh "$HOST" "lxc start ${NEW_CONTAINER}"
sleep 5

# ---- 3. set container-level env hint -------------------------------------
echo "$TAG [2/7] setting environment.CAPELLE_HOSTNAME..."
ssh "$HOST" "lxc config set ${NEW_CONTAINER} environment.CAPELLE_HOSTNAME ${NEW_CONTAINER}"

# ---- 4. write /etc/capelle-codex.env inside the new container ------------
# Important: we never print secrets to stdout. The values are piped directly
# into the container's stdin via `lxc exec`; they do NOT appear in the
# process list on gojo (only the `lxc exec` command + the here-doc content
# which is stdin, not argv).
echo "$TAG [3/7] writing /etc/capelle-codex.env inside ${NEW_CONTAINER}..."
ssh "$HOST" "lxc exec ${NEW_CONTAINER} -- bash -s" <<REMOTE
set -euo pipefail
umask 077
cat > /etc/capelle-codex.env <<ENVEOF
CAPELLE_HOSTNAME=${NEW_CONTAINER}
CAPELLE_JWT_SECRET=${CAPELLE_JWT_SECRET}
CAPELLE_STORE=${CAPELLE_STORE}
CAPELLE_POSTGRES_DSN=${CAPELLE_POSTGRES_DSN}
CAPELLE_QUEUE=${CAPELLE_QUEUE}
CAPELLE_WS_HUB=${CAPELLE_WS_HUB}
CAPELLE_RABBITMQ_URL=${CAPELLE_RABBITMQ_URL}
CAPELLE_CHROMA_HTTP=${CAPELLE_CHROMA_HTTP}
CAPELLE_PUBLIC_ORIGIN=${CAPELLE_PUBLIC_ORIGIN}
CAPELLE_ADMIN_EMAILS=${CAPELLE_ADMIN_EMAILS}
CAPELLE_SUPPORT_EMAIL=${CAPELLE_SUPPORT_EMAIL}
CAPELLE_TRUSTED_PROXIES=${CAPELLE_TRUSTED_PROXIES}
OPENAI_API_KEY=${OPENAI_API_KEY}
CODEX_ACCESS_TOKEN=${CODEX_ACCESS_TOKEN}
CODEX_REFRESH_TOKEN=${CODEX_REFRESH_TOKEN}
CODEX_BASE_URL=${CODEX_BASE_URL}
ENVEOF
chmod 600 /etc/capelle-codex.env
REMOTE

# Tell the existing systemd unit to load this env file on next start.
# If deploy-lxc.sh already wired EnvironmentFile, this is a no-op; if not,
# we splice a drop-in so we don't edit the unit itself.
ssh "$HOST" "lxc exec ${NEW_CONTAINER} -- bash -s" <<'REMOTE'
set -euo pipefail
mkdir -p /etc/systemd/system/capelle-platform.service.d
cat > /etc/systemd/system/capelle-platform.service.d/env.conf <<DROP
[Service]
EnvironmentFile=-/etc/capelle-codex.env
DROP
systemctl daemon-reload
REMOTE

# ---- 5. rejoin tailnet with the new hostname -----------------------------
echo "$TAG [4/7] joining tailnet as ${NEW_CONTAINER}..."
ssh "$HOST" "lxc exec ${NEW_CONTAINER} -- bash -s" <<REMOTE
set -euo pipefail
# A copied container still carries the template's tailscale state —
# including the private node key. ``tailscale logout`` alone leaves
# the disk state in place, so the control plane sees TWO nodes with
# the same key ("Duplicate node key" warning) and BOTH nodes lose
# their ability to route tailnet traffic cleanly. The only reliable
# fix is to wipe /var/lib/tailscale and restart tailscaled with a
# fresh state directory before rejoining — then the new hostname
# gets a fresh node key and a fresh IP.
systemctl stop tailscaled
rm -rf /var/lib/tailscale/*
systemctl start tailscaled
# Give tailscaled a moment to create its fresh state socket.
sleep 2
tailscale up --authkey='${TAILSCALE_AUTHKEY}' --hostname='${NEW_CONTAINER}' --reset
REMOTE
TS_IP="$(ssh "$HOST" "lxc exec ${NEW_CONTAINER} -- tailscale ip -4" | head -n1)"
echo "$TAG    tailnet IP: ${TS_IP}"

# ---- 6. restart the service + wait for /api/health -----------------------
echo "$TAG [5/7] restarting capelle-platform and waiting for /api/health..."
ssh "$HOST" "lxc exec ${NEW_CONTAINER} -- systemctl restart capelle-platform"

ATTEMPTS=60  # 60 * 2s = 2 min max
SLEEP_S=2
HEALTHY=0
for i in $(seq 1 $ATTEMPTS); do
    STATUS="$(ssh "$HOST" "lxc exec ${NEW_CONTAINER} -- curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/health" 2>/dev/null || echo "000")"
    if [[ "$STATUS" == "200" ]]; then
        HEALTHY=1
        echo "$TAG    /api/health -> 200 after $((i * SLEEP_S))s"
        break
    fi
    sleep "$SLEEP_S"
done
if [[ "$HEALTHY" -ne 1 ]]; then
    echo "$TAG FATAL: ${NEW_CONTAINER} did not become healthy within $((ATTEMPTS * SLEEP_S))s" >&2
    echo "$TAG diagnose with: ssh ${HOST} \"lxc exec ${NEW_CONTAINER} -- journalctl -u capelle-platform -n 50\"" >&2
    exit 1
fi

# ---- 7. register with masamichi-yaga's upstream block --------------------
# HOLD_FROM_LB=1 short-circuits: the container is healthy and on the tailnet
# but intentionally absent from the nginx upstream. Useful during v1.1
# cutover where both yutas need the new code before either serves traffic.
if [[ "${HOLD_FROM_LB:-0}" == "1" ]]; then
    echo "$TAG [6/7] HOLD_FROM_LB=1 — skipping nginx-upstream append on ${LB_CONTAINER}."
    echo "$TAG       Add ${NEW_CONTAINER} to the upstream later via:"
    echo "$TAG         ./scripts/add-to-lb.sh ${SLUG}"
    echo ""
    echo "$TAG === ${NEW_CONTAINER} spawned (held off LB) ==="
    echo "$TAG tailnet IP:  ${TS_IP}"
    echo "$TAG LB status:   NOT REGISTERED (HOLD_FROM_LB=1)"
    echo "$TAG public URL:  ${CAPELLE_PUBLIC_ORIGIN}"
    exit 0
fi

echo "$TAG [6/7] registering ${NEW_CONTAINER} with ${LB_CONTAINER}..."
# We mutate /etc/nginx/conf.d/capelle.conf in place, inserting the new
# `server` line immediately before the "# Append more yuta-* servers..."
# comment. A grep guard makes this idempotent.
ssh "$HOST" "lxc exec ${LB_CONTAINER} -- bash -s" <<REMOTE
set -euo pipefail
CONF=/etc/nginx/conf.d/capelle.conf

if grep -qE "server[[:space:]]+${NEW_CONTAINER}:8080" "\$CONF"; then
    echo "[lb] ${NEW_CONTAINER} already in upstream — skipping edit"
else
    # Insert before the append-marker comment. If the marker is missing,
    # fall back to inserting before the closing brace of the upstream block.
    if grep -qE "^[[:space:]]*# Append more yuta-" "\$CONF"; then
        sed -i "/^[[:space:]]*# Append more yuta-/i\\    server ${NEW_CONTAINER}:8080 max_fails=3 fail_timeout=30s;" "\$CONF"
    else
        sed -i "0,/^\}/s//    server ${NEW_CONTAINER}:8080 max_fails=3 fail_timeout=30s;\n}/" "\$CONF"
    fi
fi

nginx -t
systemctl reload nginx
REMOTE

# ---- 8. print the new upstream list --------------------------------------
echo "$TAG [7/7] current upstream list on ${LB_CONTAINER}:"
ssh "$HOST" "lxc exec ${LB_CONTAINER} -- awk '
    /^upstream[[:space:]]+capelle_yutas[[:space:]]*\{/ { on=1; next }
    on && /^\}/ { on=0 }
    on && /server[[:space:]]/ { print \"  \" \$0 }
' /etc/nginx/conf.d/capelle.conf"

echo ""
echo "$TAG === ${NEW_CONTAINER} spawned ==="
echo "$TAG tailnet IP:  ${TS_IP}"
echo "$TAG LB status:   healthy, registered on ${LB_CONTAINER}"
echo "$TAG public URL:  ${CAPELLE_PUBLIC_ORIGIN}"

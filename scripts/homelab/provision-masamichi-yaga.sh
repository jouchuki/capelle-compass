#!/usr/bin/env bash
# =============================================================================
# scripts/provision-masamichi-yaga.sh
# -----------------------------------------------------------------------------
# Purpose:
#   Provision the nginx load-balancer container `masamichi-yaga` on host
#   `gojo`. It fronts the yuta pool, receives traffic from Cloudflare Tunnel
#   on port 8080, and proxies to whatever hostnames are listed in the
#   `upstream capelle_yutas` block in /etc/nginx/conf.d/capelle.conf.
#
# Runs on:
#   The workstation. Drives `lxc` over SSH to `gojo`. Not run inside a
#   container.
#
# Required env:
#   TAILSCALE_AUTHKEY      Reusable Tailscale auth key.
#
# Optional env:
#   YUTA_UPSTREAMS         Comma-separated list of yuta hostnames to seed the
#                          upstream block with. Default: yuta-okkotsu.
#                          Example: YUTA_UPSTREAMS=yuta-okkotsu,yuta-maki-zenin
#   CF_TUNNEL_TOKEN        Cloudflare Tunnel token for data-compass.org. If
#                          set, cloudflared is installed + started here so this
#                          container becomes the tunnel origin; yutas never get
#                          their own cloudflared. If unset, install is skipped
#                          and the operator is expected to run scripts/
#                          cutover-tunnel.sh manually afterwards.
#
# Idempotency:
#   Early-out if the container exists. The script always re-writes
#   /etc/nginx/conf.d/capelle.conf and reloads nginx, so you can re-run to
#   re-seed the upstream list (destructive of any hand edits in that file).
# =============================================================================

set -euo pipefail

CONTAINER="${CONTAINER:-masamichi-yaga}"
HOST="${CAPELLE_LXC_HOST:-gojo}"
TAG="[provision-masamichi-yaga]"

: "${TAILSCALE_AUTHKEY:?$TAG TAILSCALE_AUTHKEY is required (reusable key from the Tailscale admin console)}"

YUTA_UPSTREAMS="${YUTA_UPSTREAMS:-yuta-okkotsu}"

# Resolve path to the repo-local nginx template (this script lives in
# scripts/, the template lives in deploy/nginx/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NGINX_TEMPLATE="${SCRIPT_DIR}/../deploy/nginx/capelle.conf"

if [[ ! -f "$NGINX_TEMPLATE" ]]; then
    echo "$TAG FATAL: nginx template not found at ${NGINX_TEMPLATE}" >&2
    exit 1
fi

echo "$TAG === Provisioning ${CONTAINER} on ${HOST} ==="
echo "$TAG seeding upstream with: ${YUTA_UPSTREAMS}"

if ssh "$HOST" "lxc info ${CONTAINER} >/dev/null 2>&1"; then
    echo "$TAG container ${CONTAINER} already exists on ${HOST} — exiting (no-op)."
    echo "$TAG (to reconfigure the upstream, edit the conf file in place or offboard + re-provision.)"
    exit 0
fi

# ---- 1. launch container ---------------------------------------------------
echo "$TAG [1/5] launching LXC container (ubuntu:24.04)..."
ssh "$HOST" "lxc launch ubuntu:24.04 ${CONTAINER} -c limits.memory=512MB -c limits.cpu=1"
sleep 5

# ---- 2. install nginx -----------------------------------------------------
echo "$TAG [2/5] installing nginx..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx >/dev/null
REMOTE

# ---- 3. build + push capelle.conf with seeded upstream list ---------------
echo "$TAG [3/5] building capelle.conf with seeded upstreams..."

# Build the `server X:8080 ...;` lines from YUTA_UPSTREAMS.
UPSTREAM_LINES=""
IFS=',' read -r -a UPSTREAMS <<<"$YUTA_UPSTREAMS"
for u in "${UPSTREAMS[@]}"; do
    u_trimmed="$(echo "$u" | tr -d '[:space:]')"
    [[ -z "$u_trimmed" ]] && continue
    UPSTREAM_LINES+="    server ${u_trimmed}:8080 max_fails=3 fail_timeout=30s;"$'\n'
done

TMP_CONF="$(mktemp)"
trap 'rm -f "$TMP_CONF"' EXIT

# Rewrite the upstream block. awk reads the template and swaps the body of
# `upstream capelle_yutas { ... }` with our freshly-built lines, keeping the
# "# Append more yuta-* servers here..." comment as a footer.
awk -v lines="$UPSTREAM_LINES" '
    BEGIN { in_block = 0 }
    /^upstream[[:space:]]+capelle_yutas[[:space:]]*\{/ {
        print
        printf "%s", lines
        print "    # Append more yuta-* servers here as they spin up."
        in_block = 1
        next
    }
    in_block == 1 {
        if ($0 ~ /^\}/) {
            print "}"
            in_block = 0
        }
        next
    }
    { print }
' "$NGINX_TEMPLATE" > "$TMP_CONF"

scp -q "$TMP_CONF" "${HOST}:/tmp/capelle.conf"
ssh "$HOST" "lxc file push /tmp/capelle.conf ${CONTAINER}/etc/nginx/conf.d/capelle.conf"
ssh "$HOST" "rm -f /tmp/capelle.conf"

# Disable the default site so port 80 doesn't answer with the Ubuntu welcome
# page (we listen on 8080 anyway; kill it for tidiness).
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<'REMOTE'
set -euo pipefail
rm -f /etc/nginx/sites-enabled/default
nginx -t
REMOTE

# ---- 4. enable + start nginx ---------------------------------------------
echo "$TAG [4/5] enabling nginx..."
ssh "$HOST" "lxc exec ${CONTAINER} -- systemctl enable --now nginx"
ssh "$HOST" "lxc exec ${CONTAINER} -- systemctl reload nginx"

# ---- 5. tailnet join ------------------------------------------------------
echo "$TAG [5/5] joining tailnet..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<REMOTE
set -euo pipefail
if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sh
fi
tailscale up --authkey='${TAILSCALE_AUTHKEY}' --hostname='${CONTAINER}' --reset
REMOTE

TS_IP="$(ssh "$HOST" "lxc exec ${CONTAINER} -- tailscale ip -4" | head -n1)"

# ---- 6. Cloudflare Tunnel (only the LB runs it; yutas do not) ------------
CF_TUNNEL_TOKEN="${CF_TUNNEL_TOKEN:-}"
if [[ -n "$CF_TUNNEL_TOKEN" ]]; then
    echo "$TAG [6/6] installing Cloudflare Tunnel..."
    ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<REMOTE
set -euo pipefail
if ! command -v cloudflared >/dev/null 2>&1; then
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
      | tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared noble main" \
      > /etc/apt/sources.list.d/cloudflared.list
    apt-get update -qq
    apt-get install -y -qq cloudflared
fi
# Idempotent: 'cloudflared service install' writes /etc/systemd/system/
# cloudflared.service with the token embedded. If it already exists with
# the same token it's a no-op, otherwise it's rewritten.
cloudflared service install '${CF_TUNNEL_TOKEN}' || true
systemctl enable --now cloudflared.service
sleep 2
systemctl is-active cloudflared.service
REMOTE
    echo "$TAG cloudflared active on ${CONTAINER}"
else
    echo "$TAG [6/6] CF_TUNNEL_TOKEN not set — skipping cloudflared install."
    echo "$TAG              Run scripts/cutover-tunnel.sh manually to swing an existing tunnel here."
fi

echo ""
echo "$TAG === ${CONTAINER} ready ==="
echo "$TAG tailnet IP:  ${TS_IP}"
echo "$TAG listen:      http://${CONTAINER}:8080"
echo "$TAG upstreams:   ${YUTA_UPSTREAMS}"
if [[ -n "$CF_TUNNEL_TOKEN" ]]; then
    echo "$TAG tunnel:      cloudflared installed + running; data-compass.org should resolve once DNS propagates"
else
    echo "$TAG next step:   flip Cloudflare Tunnel origin to http://${CONTAINER}:8080"
    echo "$TAG              (see scripts/cutover-tunnel.sh)."
fi

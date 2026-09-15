#!/usr/bin/env bash
# =============================================================================
# scripts/provision-addins-host.sh
# -----------------------------------------------------------------------------
# Provision a DEDICATED STATIC host for the Oxes Microsoft-ecosystem add-in
# bundles (Word / PowerPoint / Excel task panes), served at addins.oxesgov.nl.
#
# Unlike masamichi-yaga (an nginx *reverse proxy* to the yuta backend), this
# container serves STATIC files from /opt/addins over :8080. A Cloudflare
# tunnel terminates TLS and routes addins.oxesgov.nl -> http://localhost:8080.
# The add-ins call the Oxes backend at app.oxesgov.nl/api cross-origin (already
# allowed via Settings.addin_origins / CORS).
#
# Runs on the workstation; drives `lxc` over SSH to gojo.
#
# Required env:
#   TAILSCALE_AUTHKEY   Reusable Tailscale auth key.
#   CF_TUNNEL_TOKEN     Cloudflare Tunnel token for addins.oxesgov.nl.
#
# Optional env:
#   CONTAINER           Container name (default: addins-host).
#   CAPELLE_LXC_HOST    LXC host (default: gojo).
#
# Idempotent: early-out if the container already exists.
# =============================================================================

set -euo pipefail

CONTAINER="${CONTAINER:-addins-host}"
HOST="${CAPELLE_LXC_HOST:-gojo}"
TAG="[provision-addins-host]"

: "${TAILSCALE_AUTHKEY:?$TAG TAILSCALE_AUTHKEY is required (reusable key from the Tailscale admin console)}"
: "${CF_TUNNEL_TOKEN:?$TAG CF_TUNNEL_TOKEN is required (Cloudflare tunnel token for addins.oxesgov.nl)}"

echo "$TAG === Provisioning ${CONTAINER} on ${HOST} (static add-in host) ==="

if ssh "$HOST" "lxc info ${CONTAINER} >/dev/null 2>&1"; then
    echo "$TAG container ${CONTAINER} already exists on ${HOST} — exiting (no-op)."
    echo "$TAG (deploy bundles with scripts/deploy-addins.sh; re-provision = offboard first.)"
    exit 0
fi

# ---- 1. launch container ---------------------------------------------------
echo "$TAG [1/5] launching LXC container (ubuntu:24.04)..."
ssh "$HOST" "lxc launch ubuntu:24.04 ${CONTAINER} -c limits.memory=384MB -c limits.cpu=1"
sleep 5

# ---- 2. install nginx ------------------------------------------------------
echo "$TAG [2/5] installing nginx..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx >/dev/null
REMOTE

# ---- 3. static-serving nginx config ---------------------------------------
echo "$TAG [3/5] writing static nginx config + /opt/addins..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<'REMOTE'
set -euo pipefail
mkdir -p /opt/addins
cat > /etc/nginx/conf.d/addins.conf <<'CONF'
# Static host for the Oxes Office add-in bundles. TLS is terminated by the
# Cloudflare tunnel; this listener is plain HTTP on :8080.
server {
    listen      8080;
    server_name _;
    root        /opt/addins;

    # Content-hashed Vite assets — cache hard.
    location ~* /assets/ {
        add_header Cache-Control "public, max-age=31536000, immutable";
        try_files $uri =404;
    }

    # Everything else (taskpane.html, manifests, login pages) — revalidate.
    location / {
        add_header Cache-Control "no-cache";
        try_files $uri $uri/ =404;
    }

    # Health probe for the deploy script.
    location = /healthz { return 200 "ok\n"; add_header Content-Type text/plain; }
}
CONF
rm -f /etc/nginx/sites-enabled/default
# Landing page so the bare host returns something before bundles are deployed.
printf '<!doctype html><meta charset=utf-8><title>Oxes add-ins</title><p>Oxes add-in host.\n' > /opt/addins/index.html
nginx -t
REMOTE

# ---- 4. enable + start nginx ----------------------------------------------
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

# ---- 6. Cloudflare tunnel (addins.oxesgov.nl) -----------------------------
echo "$TAG [6/6] installing Cloudflare Tunnel..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<REMOTE
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! command -v cloudflared >/dev/null 2>&1; then
    mkdir -p --mode=0755 /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-public-v2.gpg \
      | tee /usr/share/keyrings/cloudflare-public-v2.gpg >/dev/null
    echo 'deb [signed-by=/usr/share/keyrings/cloudflare-public-v2.gpg] https://pkg.cloudflare.com/cloudflared any main' \
      > /etc/apt/sources.list.d/cloudflared.list
    apt-get update -qq
    apt-get install -y -qq cloudflared
fi
cloudflared service install '${CF_TUNNEL_TOKEN}'
systemctl enable --now cloudflared.service
sleep 3
systemctl is-active cloudflared.service
REMOTE

echo ""
echo "$TAG === ${CONTAINER} ready ==="
echo "$TAG tailnet IP:  ${TS_IP}"
echo "$TAG serves:      http://${CONTAINER}:8080  (static /opt/addins)"
echo "$TAG tunnel:      cloudflared running; map addins.oxesgov.nl -> http://localhost:8080 in the CF dashboard"
echo "$TAG next:        scripts/deploy-addins.sh to build + ship the add-in bundles"

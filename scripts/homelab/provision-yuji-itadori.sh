#!/usr/bin/env bash
# =============================================================================
# scripts/provision-yuji-itadori.sh
# -----------------------------------------------------------------------------
# Purpose:
#   Provision the standalone ChromaDB vector-store container `yuji-itadori`
#   on host `gojo`. Until v1.1, every yuta shipped with a local Chroma
#   embedded in-process — centralising it in yuta-okkotsu already cut per-job
#   RAM from 200-400 MB to ~50 MB. Pulling it out of yuta entirely means any
#   new yuta can join the pool without re-embedding or hauling the index
#   around; they all point at http://yuji-itadori:8000.
#
#   (Naming: Itadori is the strong carrier — carries Sukuna = carries the
#   index. `gojo` is already the LXD host, so it was off-limits.)
#
# Runs on:
#   The workstation. It SSHes into `gojo` and drives `lxc` from there. It
#   does NOT run inside any container.
#
# Required env:
#   TAILSCALE_AUTHKEY     Reusable auth key so the container joins the tailnet
#                         with hostname `yuji-itadori`.
#
# Idempotency:
#   If the container already exists, the script exits cleanly (does NOT
#   destroy or reconfigure — that belongs to a dedicated re-provisioner).
#   Otherwise it creates the container, installs Python 3.12 + chromadb,
#   writes the systemd unit, enables the service, and joins Tailscale.
#
# Port:
#   8000   HTTP API (yutas connect here via CAPELLE_CHROMA_HTTP=yuji-itadori:8000)
#
# Data path inside the container:
#   /var/lib/chroma   — persistent index. `scripts/migrate-chroma.sh` pushes
#                       the existing yuta-okkotsu index into this directory.
# =============================================================================

set -euo pipefail

CONTAINER="yuji-itadori"
HOST="gojo"
TAG="[provision-yuji-itadori]"

# ---- env checks ------------------------------------------------------------
: "${TAILSCALE_AUTHKEY:?$TAG TAILSCALE_AUTHKEY is required (reusable key from the Tailscale admin console)}"

echo "$TAG === Provisioning ${CONTAINER} on ${HOST} ==="

# ---- early-out if already present ------------------------------------------
if ssh "$HOST" "lxc info ${CONTAINER} >/dev/null 2>&1"; then
    echo "$TAG container ${CONTAINER} already exists on ${HOST} — exiting (no-op)."
    exit 0
fi

# ---- 1. launch container ---------------------------------------------------
echo "$TAG [1/5] launching LXC container (ubuntu:24.04)..."
ssh "$HOST" "lxc launch ubuntu:24.04 ${CONTAINER} -c limits.memory=2GB -c limits.cpu=2"
sleep 5

# ---- 2. install python3.12 + chromadb -------------------------------------
# 24.04 (noble) ships python3.12 as the default `python3`, so we just need
# venv + pip. chromadb is installed into a system-owned venv at /opt/chroma
# so the systemd unit can point at a stable interpreter path.
echo "$TAG [2/5] installing python3.12 + chromadb (latest)..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl ca-certificates >/dev/null

python3 -m venv /opt/chroma
/opt/chroma/bin/pip install --quiet --upgrade pip
# Pin a floor (0.5.0 is what the platform requirements.txt asks for) but
# take the latest compatible release so the server side tracks the
# clients the yutas run.
/opt/chroma/bin/pip install --quiet "chromadb>=0.5.0"

mkdir -p /var/lib/chroma
REMOTE

# ---- 3. systemd unit ------------------------------------------------------
# The CLI args mirror the unit we've been running inside yuta-okkotsu:
#   chroma run --path /var/lib/chroma --host 0.0.0.0 --port 8000
# Binding 0.0.0.0 is fine here: the only route in is the tailnet; gojo is
# not exposing port 8000 to the public internet.
echo "$TAG [3/5] writing chromadb.service..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<'REMOTE'
set -euo pipefail

cat >/etc/systemd/system/chromadb.service <<'UNIT'
[Unit]
Description=ChromaDB vector store (standalone, yuji-itadori)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/var/lib/chroma
ExecStart=/opt/chroma/bin/chroma run --path /var/lib/chroma --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5
# chromadb happily uses a few hundred MB with the default index; raise
# the file-descriptor cap so large collections don't trip ulimit.
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now chromadb.service
REMOTE

# ---- 4. tailnet join ------------------------------------------------------
echo "$TAG [4/5] joining tailnet..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<REMOTE
set -euo pipefail
if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sh
fi
tailscale up --authkey='${TAILSCALE_AUTHKEY}' --hostname='${CONTAINER}' --reset
REMOTE

TS_IP="$(ssh "$HOST" "lxc exec ${CONTAINER} -- tailscale ip -4" | head -n1)"

# ---- 5. smoke-test the heartbeat ------------------------------------------
# `chroma run` takes a beat to bind; give it up to ~15s before we give up.
echo "$TAG [5/5] verifying /api/v2/heartbeat..."
HEARTBEAT_OK=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
    # curl inside the container so we don't depend on the workstation's
    # tailnet DNS being warm yet.
    if ssh "$HOST" "lxc exec ${CONTAINER} -- curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/v2/heartbeat" 2>/dev/null | grep -qx 200; then
        HEARTBEAT_OK=1
        break
    fi
    sleep 2
done

echo ""
echo "$TAG === ${CONTAINER} ready ==="
echo "$TAG tailnet IP:  ${TS_IP}"
echo "$TAG hostname:    ${CONTAINER}"
echo "$TAG HTTP URL:    http://${CONTAINER}:8000"
if [[ -n "$HEARTBEAT_OK" ]]; then
    echo "$TAG heartbeat:   200 OK (verified inside container)"
else
    echo "$TAG heartbeat:   NOT YET RESPONDING — check 'ssh gojo \"lxc exec ${CONTAINER} -- journalctl -u chromadb -n 50\"'" >&2
fi
echo "$TAG confirm:     curl http://${CONTAINER}:8000/api/v2/heartbeat"
echo "$TAG next step:   run scripts/migrate-chroma.sh to move the index off yuta-okkotsu,"
echo "$TAG              then set CAPELLE_CHROMA_HTTP=${CONTAINER}:8000 on every yuta."

#!/usr/bin/env bash
# =============================================================================
# scripts/provision-kiyotaka-ijichi.sh
# -----------------------------------------------------------------------------
# Purpose:
#   Provision the shared RabbitMQ container `kiyotaka-ijichi` on host `gojo`.
#   It hosts the job queue for ohrs work and the pub/sub fan-out that lets
#   any yuta push WS events regardless of which yuta is holding the socket.
#
# Runs on:
#   The workstation. Drives `lxc` over SSH into `gojo`. Not run inside a
#   container.
#
# Required env:
#   CAPELLE_RABBITMQ_PASSWORD   Password for the `capelle` RabbitMQ user.
#   TAILSCALE_AUTHKEY           Reusable Tailscale auth key.
#
# Idempotency:
#   Early-out if the container already exists. User/vhost/permission
#   operations use RabbitMQ's idempotent `add_*` commands guarded by
#   existence checks, so re-running is safe.
#
# Ports:
#   5672   AMQP (yutas connect here)
#   15672  management UI (reachable only via tailnet)
# =============================================================================

set -euo pipefail

CONTAINER="kiyotaka-ijichi"
HOST="gojo"
TAG="[provision-kiyotaka-ijichi]"

: "${CAPELLE_RABBITMQ_PASSWORD:?$TAG CAPELLE_RABBITMQ_PASSWORD is required (password for the capelle RMQ user)}"
: "${TAILSCALE_AUTHKEY:?$TAG TAILSCALE_AUTHKEY is required (reusable key from the Tailscale admin console)}"

echo "$TAG === Provisioning ${CONTAINER} on ${HOST} ==="

if ssh "$HOST" "lxc info ${CONTAINER} >/dev/null 2>&1"; then
    echo "$TAG container ${CONTAINER} already exists on ${HOST} — exiting (no-op)."
    exit 0
fi

# ---- 1. launch container ---------------------------------------------------
echo "$TAG [1/5] launching LXC container (ubuntu:24.04)..."
ssh "$HOST" "lxc launch ubuntu:24.04 ${CONTAINER} -c limits.memory=2GB -c limits.cpu=2"
sleep 5

# ---- 2. install rabbitmq-server -------------------------------------------
echo "$TAG [2/5] installing rabbitmq-server..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq rabbitmq-server >/dev/null
systemctl enable --now rabbitmq-server
REMOTE

# ---- 3. enable management plugin ------------------------------------------
echo "$TAG [3/5] enabling management plugin on :15672..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<'REMOTE'
set -euo pipefail
rabbitmq-plugins enable rabbitmq_management
REMOTE

# ---- 4. vhost + user + permissions ----------------------------------------
# Password passed via env so it never appears in the ssh argv / gojo ps.
echo "$TAG [4/5] creating vhost + user 'capelle' and granting permissions..."
ssh "$HOST" "lxc exec ${CONTAINER} -- env CAPELLE_RABBITMQ_PASSWORD=${CAPELLE_RABBITMQ_PASSWORD} bash -s" <<'REMOTE'
set -euo pipefail
: "${CAPELLE_RABBITMQ_PASSWORD:?rmq password missing inside container}"

# vhost
if ! rabbitmqctl list_vhosts --no-table-headers | awk '{print $1}' | grep -qx capelle; then
    rabbitmqctl add_vhost capelle
fi

# user
if ! rabbitmqctl list_users --no-table-headers | awk '{print $1}' | grep -qx capelle; then
    rabbitmqctl add_user capelle "${CAPELLE_RABBITMQ_PASSWORD}"
else
    rabbitmqctl change_password capelle "${CAPELLE_RABBITMQ_PASSWORD}"
fi

# full access on the vhost: configure, write, read = .*
rabbitmqctl set_permissions -p capelle capelle ".*" ".*" ".*"

# give it a non-privileged tag so it can hit the mgmt API for its own vhost
rabbitmqctl set_user_tags capelle management

# Delete default guest user so no plaintext guest:guest is left around.
if rabbitmqctl list_users --no-table-headers | awk '{print $1}' | grep -qx guest; then
    rabbitmqctl delete_user guest
fi
REMOTE

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

echo ""
echo "$TAG === ${CONTAINER} ready ==="
echo "$TAG tailnet IP:  ${TS_IP}"
echo "$TAG AMQP URL:    amqp://capelle:<password>@${CONTAINER}:5672/capelle"
echo "$TAG Mgmt UI:     http://${CONTAINER}:15672  (user: capelle)"
echo "$TAG next step:   set CAPELLE_RABBITMQ_URL on the yutas."

#!/usr/bin/env bash
# Remove Capelle Data Assistent LXC container from the cluster
# Usage: bash scripts/offboard-lxc.sh [container-name]
#
# This will:
#   1. Stop the container
#   2. Remove it from Tailscale (logout)
#   3. Delete the container and all its data

set -euo pipefail

CONTAINER="${1:-yuta-okkotsu}"
HOST="gojo"

echo "=== Offboarding ${CONTAINER} from ${HOST} ==="

# 1. Logout from Tailscale (removes from tailnet)
echo "[1/3] Removing from Tailscale..."
ssh "$HOST" "lxc exec ${CONTAINER} -- tailscale logout 2>/dev/null || true"

# 2. Stop container
echo "[2/3] Stopping container..."
ssh "$HOST" "lxc stop ${CONTAINER} --force 2>/dev/null || true"

# 3. Delete container
echo "[3/3] Deleting container..."
ssh "$HOST" "lxc delete ${CONTAINER} --force"

echo ""
echo "=== ${CONTAINER} removed ==="
echo "The Tailscale device may still show as 'offline' in the admin console."
echo "Remove it manually at https://login.tailscale.com/admin/machines if needed."

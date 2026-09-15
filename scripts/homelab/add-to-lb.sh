#!/usr/bin/env bash
# =============================================================================
# scripts/add-to-lb.sh
# -----------------------------------------------------------------------------
# Purpose:
#   Register an already-running `yuta-<slug>` container with the masamichi-yaga
#   nginx load balancer by appending a `server` line to the `capelle_yutas`
#   upstream block in /etc/nginx/conf.d/capelle.conf, then reloading nginx.
#
#   This is split out from spawn-yuta.sh so we can park a newly cloned yuta
#   off the LB (HOLD_FROM_LB=1) during v1.1 code cutover and flip it on only
#   once v1.1 is live on both yutas.
#
# Usage:
#   ./scripts/add-to-lb.sh <slug>     # e.g. ./scripts/add-to-lb.sh maki-zenin
#
# Idempotent: a grep guard prevents duplicate `server` lines; re-running
# is a no-op.
# =============================================================================

set -euo pipefail

TAG="[add-to-lb]"
HOST="${HOST:-gojo}"
LB_CONTAINER="${LB_CONTAINER:-masamichi-yaga}"

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "$(ts) $TAG $*"; }

if [[ $# -ne 1 ]]; then
    echo "$TAG usage: $0 <slug>   (e.g. $0 maki-zenin)" >&2
    exit 2
fi

SLUG="$1"
if [[ ! "$SLUG" =~ ^[a-z0-9-]+$ ]]; then
    echo "$TAG FATAL: slug '${SLUG}' must match ^[a-z0-9-]+\$" >&2
    exit 2
fi

NEW_CONTAINER="yuta-${SLUG}"

log "adding ${NEW_CONTAINER} to nginx upstream on ${LB_CONTAINER}..."

ssh "$HOST" "lxc exec ${LB_CONTAINER} -- bash -s" <<REMOTE
set -euo pipefail
CONF=/etc/nginx/conf.d/capelle.conf

if grep -qE "server[[:space:]]+${NEW_CONTAINER}:8080" "\$CONF"; then
    echo "[add-to-lb] ${NEW_CONTAINER} already in upstream — no-op"
else
    if grep -qE "^[[:space:]]*# Append more yuta-" "\$CONF"; then
        sed -i "/^[[:space:]]*# Append more yuta-/i\\    server ${NEW_CONTAINER}:8080 max_fails=3 fail_timeout=30s;" "\$CONF"
    else
        sed -i "0,/^\}/s//    server ${NEW_CONTAINER}:8080 max_fails=3 fail_timeout=30s;\n}/" "\$CONF"
    fi
    echo "[add-to-lb] inserted server line for ${NEW_CONTAINER}"
fi

nginx -t
systemctl reload nginx
REMOTE

log "current upstream list on ${LB_CONTAINER}:"
ssh "$HOST" "lxc exec ${LB_CONTAINER} -- awk '
    /^upstream[[:space:]]+capelle_yutas[[:space:]]*\{/ { on=1; next }
    on && /^\}/ { on=0 }
    on && /server[[:space:]]/ { print \"  \" \$0 }
' /etc/nginx/conf.d/capelle.conf"

log "done."

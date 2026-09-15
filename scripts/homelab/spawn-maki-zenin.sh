#!/usr/bin/env bash
# =============================================================================
# scripts/spawn-maki-zenin.sh
# -----------------------------------------------------------------------------
# Purpose:
#   Bring up the second production yuta — `yuta-maki-zenin` — as a clone of the
#   `yuta-okkotsu` template, BUT hold it off the nginx upstream block until
#   the v1.1 code (RabbitMQ queue/hub + shared Postgres + centralised Chroma)
#   has been deployed onto both yutas. If the LB were to start routing to a
#   maki-zenin running v1.0.1 code against v1.1 infra, the first request that
#   hit it would crash the worker — so we keep it parked.
#
# This is a thin wrapper around `./scripts/spawn-yuta.sh maki-zenin` with
# HOLD_FROM_LB=1, plus:
#   - idempotent: if `yuta-maki-zenin` already exists on `gojo`, report its
#     current /api/health status rather than re-spawning (which would fail).
#   - crisp next-steps message at the end telling the operator how to bring
#     the container into the LB after v1.1 is live on both yutas.
#
# Usage:
#   # spawn-yuta.sh's required env must be in scope (CAPELLE_JWT_SECRET,
#   # CAPELLE_POSTGRES_DSN, CAPELLE_RABBITMQ_URL, CAPELLE_CHROMA_HTTP,
#   # CAPELLE_ADMIN_EMAILS, CAPELLE_SUPPORT_EMAIL, OPENAI_API_KEY,
#   # TAILSCALE_AUTHKEY).
#   ./scripts/spawn-maki-zenin.sh
#
# Safe to run multiple times: no-ops when the container already exists.
# =============================================================================

set -euo pipefail

TAG="[spawn-maki-zenin]"
HOST="gojo"
SLUG="maki-zenin"
NEW_CONTAINER="yuta-${SLUG}"

ts() { date -u +'%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "$(ts) $TAG $*"; }

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" && pwd )"

# ---- idempotency gate ------------------------------------------------------
# If the container is already on gojo, skip spawn and just report health.
# spawn-yuta.sh itself would exit 1 on a pre-existing container, which is
# fine during fresh runs but annoying when operators re-invoke us to verify
# state — so we short-circuit here with a friendlier status readout.
if ssh "$HOST" "lxc info ${NEW_CONTAINER} >/dev/null 2>&1"; then
    log "container ${NEW_CONTAINER} already exists on ${HOST} — skipping spawn."

    STATUS="$(ssh "$HOST" \
        "lxc exec ${NEW_CONTAINER} -- curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/health" \
        2>/dev/null || echo "000")"
    log "existing /api/health -> ${STATUS}"

    TS_IP="$(ssh "$HOST" "lxc exec ${NEW_CONTAINER} -- tailscale ip -4" 2>/dev/null | head -n1 || true)"
    log "existing tailnet IP: ${TS_IP:-<unknown>}"

    log "yuta-maki-zenin is up on the tailnet but not in the nginx upstream."
    log "Add it via \`./scripts/add-to-lb.sh maki-zenin\` once v1.1 code is deployed there."
    exit 0
fi

# ---- primary path ----------------------------------------------------------
log "spawning ${NEW_CONTAINER} with HOLD_FROM_LB=1 (will NOT be added to nginx upstream)..."
HOLD_FROM_LB=1 "${SCRIPT_DIR}/spawn-yuta.sh" "${SLUG}"

# ---- next-steps message ----------------------------------------------------
echo ""
log "==============================================================="
log "yuta-maki-zenin is up on the tailnet but not in the nginx upstream."
log "Add it via \`./scripts/add-to-lb.sh maki-zenin\` once v1.1 code is deployed there."
log "==============================================================="

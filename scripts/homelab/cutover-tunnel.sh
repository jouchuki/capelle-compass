#!/usr/bin/env bash
# =============================================================================
# scripts/cutover-tunnel.sh
# -----------------------------------------------------------------------------
# Purpose:
#   Flip the Cloudflare Tunnel origin from the single-instance URL
#     http://yuta-okkotsu:8080
#   to the load-balancer URL
#     http://masamichi-yaga:8080
#   by rewriting the `service:` line of the cloudflared config, and reloading
#   the daemon. Designed to be run exactly once during the cutover.
#
# Runs on:
#   The workstation. SSHes into `gojo` to drive lxc exec against the
#   container that hosts cloudflared.
#
# Required flags:
#   --yes      Acknowledge that this is destructive (rewrites config on the
#              live prod container) and proceed. Without it the script only
#              prints a preview diff.
#
# Optional env:
#   CF_CONTAINER       Container that hosts cloudflared. Default: yuta-okkotsu.
#                      (deploy-lxc.sh installs cloudflared inside the yuta
#                      container itself, so that is where the config lives.)
#   NEW_ORIGIN         Default: http://masamichi-yaga:8080
#   OLD_ORIGIN_MATCH   Regex used to locate the current service line.
#                      Default: ^[[:space:]]*service:[[:space:]]*http.*:8080[[:space:]]*$
#
# Idempotency:
#   If the service line already points at NEW_ORIGIN, the script exits
#   cleanly without touching the file or reloading cloudflared.
# =============================================================================

set -euo pipefail

TAG="[cutover-tunnel]"
HOST="gojo"
CF_CONTAINER="${CF_CONTAINER:-yuta-okkotsu}"
NEW_ORIGIN="${NEW_ORIGIN:-http://masamichi-yaga:8080}"
OLD_ORIGIN_MATCH="${OLD_ORIGIN_MATCH:-^[[:space:]]*service:[[:space:]]*http.*:8080[[:space:]]*\$}"

CONFIRM=0
for arg in "$@"; do
    case "$arg" in
        --yes) CONFIRM=1 ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)
            echo "$TAG unknown arg: ${arg}" >&2
            exit 2
            ;;
    esac
done

# ---- 1. locate the cloudflared config file -------------------------------
echo "$TAG locating cloudflared config inside ${CF_CONTAINER}..."
# `cloudflared service install` lays down the config under
# /etc/cloudflared/ or sometimes /root/.cloudflared/ depending on version.
# Check both; prefer /etc.
CF_CONFIG="$(ssh "$HOST" "lxc exec ${CF_CONTAINER} -- bash -c '
for p in /etc/cloudflared/config.yml /etc/cloudflared/config.yaml /root/.cloudflared/config.yml /root/.cloudflared/config.yaml; do
    if [ -f \"\$p\" ]; then echo \"\$p\"; exit 0; fi
done
exit 1
' 2>/dev/null" || true)"

if [[ -z "$CF_CONFIG" ]]; then
    echo "$TAG FATAL: no cloudflared config found in ${CF_CONTAINER}." >&2
    echo "$TAG inspect:   ssh ${HOST} \"lxc exec ${CF_CONTAINER} -- systemctl status cloudflared.service\"" >&2
    echo "$TAG hint:      if cloudflared was installed as 'cloudflared service install <token>'" >&2
    echo "$TAG            the config lives in the Cloudflare dashboard, not on disk — flip the" >&2
    echo "$TAG            origin in the dashboard's Public Hostnames page instead." >&2
    exit 1
fi
echo "$TAG found config: ${CF_CONFIG}"

# ---- 2. show current service line + diff ---------------------------------
CURRENT_LINE="$(ssh "$HOST" "lxc exec ${CF_CONTAINER} -- grep -E '^[[:space:]]*service:' '${CF_CONFIG}'" || true)"
if [[ -z "$CURRENT_LINE" ]]; then
    echo "$TAG FATAL: no 'service:' line found in ${CF_CONFIG}." >&2
    exit 1
fi
echo "$TAG current:  ${CURRENT_LINE}"
echo "$TAG new:      service: ${NEW_ORIGIN}"

# Already pointing at the new origin? Nothing to do.
if echo "$CURRENT_LINE" | grep -qF "${NEW_ORIGIN}"; then
    echo "$TAG service line already points at ${NEW_ORIGIN} — no changes."
    echo "$TAG Tunnel origin is → masamichi-yaga. Test with: curl https://data-compass.org/api/health"
    exit 0
fi

if [[ "$CONFIRM" -ne 1 ]]; then
    echo ""
    echo "$TAG PREVIEW ONLY. Re-run with --yes to apply."
    exit 0
fi

# ---- 3. rewrite in place on the container --------------------------------
echo "$TAG applying rewrite..."
# We use sed -E with an extended regex. The replacement preserves any
# leading whitespace via capture group \1.
ssh "$HOST" "lxc exec ${CF_CONTAINER} -- bash -s" <<REMOTE
set -euo pipefail
CF="${CF_CONFIG}"
cp -a "\$CF" "\${CF}.pre-cutover.\$(date +%s).bak"
sed -i -E "s|^([[:space:]]*)service:[[:space:]]+http.*|\1service: ${NEW_ORIGIN}|" "\$CF"
grep -E '^[[:space:]]*service:' "\$CF"
REMOTE

# ---- 4. reload cloudflared -----------------------------------------------
echo "$TAG reloading cloudflared..."
ssh "$HOST" "lxc exec ${CF_CONTAINER} -- systemctl reload cloudflared || lxc exec ${CF_CONTAINER} -- systemctl restart cloudflared"

sleep 2
ssh "$HOST" "lxc exec ${CF_CONTAINER} -- systemctl is-active cloudflared" || {
    echo "$TAG FATAL: cloudflared is not active after reload." >&2
    exit 1
}

echo ""
echo "$TAG Tunnel origin now → masamichi-yaga. Test with: curl https://data-compass.org/api/health"

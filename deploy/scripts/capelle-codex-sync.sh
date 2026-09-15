#!/usr/bin/env bash
# =============================================================================
# capelle-codex-sync.sh
# -----------------------------------------------------------------------------
# Unified per-yuta Codex token sync. Runs IDENTICALLY on every yuta — no
# owner/follower split, no cross-yuta ssh channel, no handoff file. Each yuta
# has its own `codex login --device-auth` session (its own independent
# refresh_token), so syncing is a strictly local affair: snap auth.json ->
# /etc/capelle-codex.env.
#
# On each tick:
#   1. Read tokens from /root/snap/codex/current/auth.json.
#   2. Decode the access_token's JWT `exp` claim; if less than 24h remains,
#      emit a WARN line so the admin dashboard can surface "manual re-auth
#      needed" before users start seeing 401s.
#   3. If the access_token differs from the current env file value, rewrite
#      /etc/capelle-codex.env (preserving every non-CODEX key) and restart
#      capelle-platform.service so ohrs subprocesses inherit the fresh pair.
#
# Logs one structured line per tick to journald via stdout:
#   - "in_sync access_tail=XXXXXXXX"
#   - "rotated access_tail=OLD8->NEW8 service_restarted=yes"
#   - "WARN access_token_near_expiry hours_remaining=H.H — run: codex login --device-auth"
#   - "FATAL auth_json_missing ..."  (when `codex login --device-auth` has
#                                      never been run on this yuta)
#
# Managed + installed by deploy/scripts/install-codex-sync.sh, which embeds
# this exact script as a heredoc. Keep this file as the single source of
# truth for the body; edits here must be mirrored into the installer's
# heredoc block.
#
# See deploy/INFRA_AUDIT.md for the architectural rationale.
# =============================================================================

set -euo pipefail

AUTH_JSON="/root/snap/codex/current/auth.json"
ENV_FILE="/etc/capelle-codex.env"
SERVICE="capelle-platform.service"
EXPIRY_WARN_HOURS=24

tail8() {
    local s="${1:-}"
    if [[ -z "$s" ]]; then printf '<empty>'
    elif (( ${#s} <= 8 )); then printf '%s' "$s"
    else printf '%s' "${s: -8}"
    fi
}
log() { printf 'capelle-codex-sync %s\n' "$*"; }

if [[ ! -r "$AUTH_JSON" ]]; then
    log "FATAL auth_json_missing path=${AUTH_JSON} — run: codex login --device-auth"
    exit 1
fi

# Parse tokens + decode JWT exp claim in one python invocation. Keeps
# secrets out of argv; tokens travel via stdout into a bash array.
read_and_check() {
    python3 - "$AUTH_JSON" <<'PY'
import json, sys, time, base64
try:
    d = json.load(open(sys.argv[1]))
except Exception as e:
    sys.exit(f"parse_error: {e}")
t = d.get("tokens") or {}
a = t.get("access_token") or ""
r = t.get("refresh_token") or ""
if not a or not r:
    sys.exit("empty tokens in auth.json")
exp_secs = ""
parts = a.split(".")
if len(parts) == 3:
    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pad))
        exp = payload.get("exp")
        if exp:
            exp_secs = int(exp - time.time())
    except Exception:
        pass
print(a)
print(r)
print(exp_secs)
PY
}

mapfile -t OUT < <(read_and_check)
if (( ${#OUT[@]} < 2 )); then
    log "FATAL token_parse_failed"
    exit 2
fi
NEW_ACCESS="${OUT[0]}"
NEW_REFRESH="${OUT[1]}"
EXP_SECS="${OUT[2]:-}"

if [[ -n "$EXP_SECS" ]] && (( EXP_SECS < EXPIRY_WARN_HOURS * 3600 )); then
    HRS=$(awk "BEGIN { printf \"%.1f\", $EXP_SECS / 3600 }")
    log "WARN access_token_near_expiry hours_remaining=${HRS} — run: codex login --device-auth"
fi

CUR_ACCESS=""
if [[ -r "$ENV_FILE" ]]; then
    CUR_ACCESS="$(grep -E '^CODEX_ACCESS_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
fi

NEW_TAIL="$(tail8 "$NEW_ACCESS")"
OLD_TAIL="$(tail8 "$CUR_ACCESS")"

if [[ "$NEW_ACCESS" == "$CUR_ACCESS" ]]; then
    log "in_sync access_tail=${NEW_TAIL}"
    exit 0
fi

tmp="$(mktemp)"
chmod 0600 "$tmp"
if [[ -f "$ENV_FILE" ]]; then
    grep -vE '^CODEX_(ACCESS|REFRESH)_TOKEN=' "$ENV_FILE" > "$tmp" || true
fi
{
    printf 'CODEX_ACCESS_TOKEN=%s\n' "$NEW_ACCESS"
    printf 'CODEX_REFRESH_TOKEN=%s\n' "$NEW_REFRESH"
} >> "$tmp"
chown root:root "$tmp"
mv "$tmp" "$ENV_FILE"
chmod 0600 "$ENV_FILE"

if systemctl restart "$SERVICE"; then
    log "rotated access_tail=${OLD_TAIL}->${NEW_TAIL} service_restarted=yes"
    exit 0
else
    log "rotated access_tail=${OLD_TAIL}->${NEW_TAIL} service_restarted=FAILED"
    exit 3
fi

#!/usr/bin/env bash
# =============================================================================
# install-codex-sync.sh
# -----------------------------------------------------------------------------
# Idempotent per-yuta installer for the Codex token-sync timer. Runs
# IDENTICALLY on every yuta — no role argument, no cross-yuta coupling.
# Each yuta has its own `codex login --device-auth` session and syncs its
# own snap auth.json into /etc/capelle-codex.env.
#
# Designed to be fed to `lxc exec <yuta> -- bash -s` over stdin; the sync
# script body is embedded as a heredoc so no other files need copying.
#
# Also (idempotently):
#   - scrubs OPENAI_API_KEY from /etc/systemd/system/capelle-platform.service
#     and /etc/capelle-codex.env (INFRA audit §6 finding 5; never restored)
#   - removes legacy owner/follower artifacts from the previous architecture:
#     the forced-command ssh authorized_keys entry on the former owner, the
#     id_codex_sync key on the former follower, /var/lib/capelle/ handoff
#     dir, and the redundant env.conf systemd drop-in
#
# See deploy/INFRA_AUDIT.md for the architectural rationale.
# =============================================================================

set -euo pipefail

if [[ $# -gt 0 && ( "$1" == "owner" || "$1" == "follower" ) ]]; then
    echo "[install-codex-sync] NOTE: role arg ignored — architecture is per-yuta independent now" >&2
fi

SERVICE_PATH="/etc/systemd/system/capelle-codex-sync.service"
TIMER_PATH="/etc/systemd/system/capelle-codex-sync.timer"
SYNC_BIN="/usr/local/sbin/capelle-codex-sync"

echo "[install-codex-sync] host=$(hostname)"

# ------------------------------------------------------------------
# 1. Write the unified sync script into /usr/local/sbin/capelle-codex-sync
# ------------------------------------------------------------------
install -d -m 0755 /usr/local/sbin

cat >"$SYNC_BIN" <<'SYNC_EOF'
#!/usr/bin/env bash
# Unified capelle-codex-sync. Managed by install-codex-sync.sh.
# Source of truth: capelle-deploy/deploy/scripts/capelle-codex-sync.sh
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
SYNC_EOF

chmod 0755 "$SYNC_BIN"
chown root:root "$SYNC_BIN"

# ------------------------------------------------------------------
# 2. Write the systemd unit + timer
# ------------------------------------------------------------------
cat >"$SERVICE_PATH" <<'UNIT_EOF'
[Unit]
Description=Capelle Codex token sync (snap auth.json -> /etc/capelle-codex.env)
Documentation=file:///home/jouchuki2/hobby/capelle-deploy/deploy/INFRA_AUDIT.md
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/capelle-codex-sync
User=root
Group=root
ProtectSystem=false
ProtectHome=read-only
NoNewPrivileges=yes
StandardOutput=journal
StandardError=journal
UNIT_EOF

cat >"$TIMER_PATH" <<'TIMER_EOF'
[Unit]
Description=Run capelle-codex-sync every 5 minutes
Documentation=file:///home/jouchuki2/hobby/capelle-deploy/deploy/INFRA_AUDIT.md

[Timer]
OnBootSec=30s
OnUnitActiveSec=5min
Persistent=true
Unit=capelle-codex-sync.service
AccuracySec=15s

[Install]
WantedBy=timers.target
TIMER_EOF

chmod 0644 "$SERVICE_PATH" "$TIMER_PATH"
chown root:root "$SERVICE_PATH" "$TIMER_PATH"

# ------------------------------------------------------------------
# 3. Scrub OPENAI_API_KEY from the capelle-platform unit file
#    (INFRA audit §6 finding 5). Templates never reintroduce it.
# ------------------------------------------------------------------
CAPELLE_UNIT="/etc/systemd/system/capelle-platform.service"
if [[ -f "$CAPELLE_UNIT" ]] && grep -qE '^Environment=OPENAI_API_KEY=' "$CAPELLE_UNIT"; then
    echo "[install-codex-sync] stripping OPENAI_API_KEY line from ${CAPELLE_UNIT}"
    cp -a "$CAPELLE_UNIT" "${CAPELLE_UNIT}.bak-$(date +%s)"
    sed -i '/^Environment=OPENAI_API_KEY=/d' "$CAPELLE_UNIT"
fi

# ------------------------------------------------------------------
# 4. Scrub OPENAI_API_KEY from /etc/capelle-codex.env. The sync script
#    preserves non-CODEX keys on rewrite, so a stray entry would
#    persist forever; delete it here on every install.
# ------------------------------------------------------------------
CAPELLE_ENV="/etc/capelle-codex.env"
if [[ -f "$CAPELLE_ENV" ]] && grep -qE '^OPENAI_API_KEY=' "$CAPELLE_ENV"; then
    echo "[install-codex-sync] stripping OPENAI_API_KEY line from ${CAPELLE_ENV}"
    tmp="$(mktemp)"
    chmod 0600 "$tmp"
    grep -vE '^OPENAI_API_KEY=' "$CAPELLE_ENV" > "$tmp"
    chown root:root "$tmp"
    mv "$tmp" "$CAPELLE_ENV"
    chmod 0600 "$CAPELLE_ENV"
fi

# ------------------------------------------------------------------
# 5. Remove redundant env.conf drop-in if present (legacy follower path).
# ------------------------------------------------------------------
DROPIN="/etc/systemd/system/capelle-platform.service.d/env.conf"
if [[ -f "$DROPIN" ]] && grep -qE '^EnvironmentFile=-?/etc/capelle-codex.env' "$DROPIN"; then
    echo "[install-codex-sync] removing redundant drop-in ${DROPIN}"
    rm -f "$DROPIN"
fi

# ------------------------------------------------------------------
# 6. Purge legacy owner/follower artifacts from the previous architecture.
#    Safe no-ops when the paths don't exist.
# ------------------------------------------------------------------
LEGACY_HANDOFF="/var/lib/capelle/codex-tokens.env"
LEGACY_HANDOFF_DIR="/var/lib/capelle"
LEGACY_SSH_KEY="/root/.ssh/id_codex_sync"
LEGACY_SSH_KEY_PUB="/root/.ssh/id_codex_sync.pub"
LEGACY_SSH_KH="/root/.ssh/known_hosts_codex_sync"
AUTHORIZED_KEYS="/root/.ssh/authorized_keys"

rm -f "$LEGACY_HANDOFF" "$LEGACY_SSH_KEY" "$LEGACY_SSH_KEY_PUB" "$LEGACY_SSH_KH"
# Only remove the dir if empty (other Capelle state could live here in future).
if [[ -d "$LEGACY_HANDOFF_DIR" ]] && [[ -z "$(ls -A "$LEGACY_HANDOFF_DIR" 2>/dev/null || true)" ]]; then
    rmdir "$LEGACY_HANDOFF_DIR"
fi

# Strip the forced-command authorized_keys entry (a single line containing
# 'codex-sync-follower' in the comment). Only rewrites the file when the
# pattern matches, to avoid clobbering unrelated keys.
if [[ -f "$AUTHORIZED_KEYS" ]] && grep -q 'codex-sync-follower' "$AUTHORIZED_KEYS"; then
    echo "[install-codex-sync] removing legacy forced-command entry from ${AUTHORIZED_KEYS}"
    tmp="$(mktemp)"
    chmod 0600 "$tmp"
    grep -v 'codex-sync-follower' "$AUTHORIZED_KEYS" > "$tmp" || true
    chown root:root "$tmp"
    mv "$tmp" "$AUTHORIZED_KEYS"
    chmod 0600 "$AUTHORIZED_KEYS"
fi

# ------------------------------------------------------------------
# 7. Daemon-reload + enable + start the timer
# ------------------------------------------------------------------
systemctl daemon-reload
systemctl enable --now capelle-codex-sync.timer

# Fire the oneshot once immediately so the env file is refreshed on install,
# not at the next 5-minute tick. Don't fail the installer if this yuta
# hasn't been logged in yet — the FATAL in the script is informative, and
# the timer will retry every 5 min.
if ! systemctl start capelle-codex-sync.service; then
    echo "[install-codex-sync] NOTE first-run oneshot returned non-zero"
    echo "[install-codex-sync]   this is expected if 'codex login --device-auth' has not been run yet"
    echo "[install-codex-sync]   the timer will retry every 5 min"
fi

echo "[install-codex-sync] done"
systemctl list-timers --no-pager | awk 'NR==1 || /capelle-codex-sync/' || true

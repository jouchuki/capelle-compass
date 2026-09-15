#!/usr/bin/env bash
# scripts/sync-ohrs-settings.sh — ship the canonical ohrs *permission* block onto
# each yuta's /opt/capelle-repo-agent/.openharnessrs/settings.json.
#
# WHY a dedicated step: neither deploy/push.sh nor capelle-repo-agent/sync-to-
# yutas.sh carries this file — push.sh ships code, sync-to-yutas ships tools/
# skills/data. The permission block (allowed_tools / denied_tools / path_rules /
# denied_commands) that gates which ohrs tools the groeikern agent may call was
# therefore a manual, drift-prone edit. This makes it part of `make deploy`.
#
# WHAT it touches: ONLY `.permission`. The host file's provider/model (Codex
# routing), enabled_plugins, hooks (save_output.py path), and memory are
# preserved by a jq field-merge — the local source deliberately omits/diverges
# on those, so a wholesale copy would clobber prod. No service restart is needed:
# impl_ohrs.py re-materialises the per-job settings (folding this file in) on
# every job.
#
# Source of truth: the groeikern working-dir settings on this workstation,
#   ${CAPELLE_OHRS_WORKING_DIR:-../capelle-repo-agent}/.openharnessrs/settings.json
# Override with OHRS_SETTINGS_SRC=/path/to/settings.json.
#
# Usage:
#   scripts/sync-ohrs-settings.sh [all|yuta-<name>] [--dry-run]
# Transport (ssh-into-host + running-yuta enumeration) is reused from
# deploy/lib/push-lib.sh — do NOT reimplement ssh/ProxyJump here.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/deploy/lib/push-lib.sh"

HOST="${CAPELLE_LXC_HOST:-gojo}"
REMOTE_SETTINGS="/opt/capelle-repo-agent/.openharnessrs/settings.json"

TARGET="all"
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        all|yuta-*) TARGET="$arg" ;;
        *) log_fail "unknown arg: $arg (want 'all', 'yuta-<name>', or --dry-run)"; exit 2 ;;
    esac
done

SRC="${OHRS_SETTINGS_SRC:-${CAPELLE_OHRS_WORKING_DIR:-$REPO_ROOT/../capelle-repo-agent}/.openharnessrs/settings.json}"

command -v jq >/dev/null || { log_fail "jq is required on the workstation"; exit 2; }
[[ -f "$SRC" ]] || { log_fail "source settings not found: $SRC"; exit 2; }

PERM="$(jq -c '.permission' "$SRC")"
[[ "$PERM" != "null" && -n "$PERM" ]] || { log_fail "no .permission object in $SRC"; exit 2; }

log_info "source:     $SRC"
log_info "permission: $PERM"
log_info "remote:     $REMOTE_SETTINGS"

if [[ "$TARGET" == "all" ]]; then
    mapfile -t YUTAS < <(list_running_yutas "$HOST")
else
    YUTAS=("$TARGET")
fi
[[ ${#YUTAS[@]} -gt 0 ]] || { log_fail "no target yutas on ${HOST}"; exit 1; }
log_info "target:     ${YUTAS[*]}"

FAILED=()
for yuta in "${YUTAS[@]}"; do
    log_info "yuta: ${yuta}"
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "[dry-run] would set .permission on ${yuta}:${REMOTE_SETTINGS}"
        continue
    fi
    # Remote jq merge: replace ONLY .permission, validate, atomic mv. PERM is
    # expanded locally into a single-quoted --argjson literal (JSON has no
    # single quotes, so this is safe); remote-side vars are escaped (\$).
    if ! ssh "$HOST" "lxc exec ${yuta} -- bash -s" <<REMOTE
set -euo pipefail
command -v jq >/dev/null || { echo "jq missing on ${yuta}" >&2; exit 3; }
f="${REMOTE_SETTINGS}"
[[ -f "\$f" ]] || { echo "settings not found: \$f" >&2; exit 3; }
tmp="\$(mktemp)"
jq --argjson perm '${PERM}' '.permission = \$perm' "\$f" > "\$tmp"
# Guard: result must stay valid AND keep prod-only fields we must not drop.
jq -e '.provider and .model and .permission.allowed_tools' "\$tmp" >/dev/null
mv "\$tmp" "\$f"
printf 'allowed=%s denied=%s\n' \
    "\$(jq -c '.permission.allowed_tools' "\$f")" \
    "\$(jq -c '.permission.denied_tools' "\$f")"
REMOTE
    then
        log_fail "${yuta}: permission sync failed"
        FAILED+=("$yuta")
        continue
    fi
    log_ok "${yuta}: permission synced"
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
    log_fail "failed: ${FAILED[*]}"
    exit 1
fi
log_ok "ohrs permission synced to: ${YUTAS[*]}"

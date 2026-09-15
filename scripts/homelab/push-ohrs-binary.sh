#!/usr/bin/env bash
# scripts/push-ohrs-binary.sh — deploy a locally-built `oh`/`ohrs` binary to the
# yutas, verified by sha and with a backup of the previous binary.
#
# WHY: there is no oh build pipeline. Prod's /usr/local/bin/ohrs is hand-copied
# (bootstrap-yuta.sh sources it FROM yuta-okkotsu), so binary rolls were ad-hoc.
# This makes the push repeatable, sha-verified, and backed up.
#
# The new binary is streamed to a temp path, its sha checked against the local
# one, then atomically `mv`d over the live binary — a rename, so a running ohrs
# job keeps its old inode and only new spawns pick up the new binary. No service
# restart needed (ohrs is spawned per job).
#
# Usage:
#   scripts/push-ohrs-binary.sh [all|yuta-<name>] [--dry-run]
# Env:
#   OHRS_BIN  path to the binary to push
#             (default: $HOME/safetrace/openharness-rs/target/release/ohrs)
#   OHRS_PROXYJUMP  jump host for the 11MB stream (default: griffith-lan).
#             The default `gojo` ssh alias jumps via `griffith`, which resolves
#             to the TAILSCALE IP (DERP-relayed, slow for bulk). griffith-lan
#             (192.168.178.60) keeps the workstation->griffith hop on the LAN.
#             Set empty to use the host's own ProxyJump.
# Transport (running-yuta enumeration) reused from deploy/lib/push-lib.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO_ROOT/deploy/lib/push-lib.sh"

HOST="${CAPELLE_LXC_HOST:-gojo}"
REMOTE="/usr/local/bin/ohrs"
OHRS_BIN="${OHRS_BIN:-$HOME/safetrace/openharness-rs/target/release/ohrs}"

# Force the bulk transfer over the LAN, not tailscale. SSH is an array so the
# overrides apply to every connection this script opens. A DEDICATED control
# socket is essential: gojo has `ControlMaster auto`, so without an isolated
# ControlPath the -o ProxyJump override silently reuses the default (tailscale)
# master. The dedicated master is set up once over the LAN and reused for the
# 11MB stream + verification calls, then torn down at exit.
OHRS_PROXYJUMP="${OHRS_PROXYJUMP-griffith-lan}"
_CM="${TMPDIR:-/tmp}/cm-ohrs-%r@%h:%p"
SSH=(ssh -o "ControlPath=$_CM" -o ControlMaster=auto -o ControlPersist=120)
[[ -n "$OHRS_PROXYJUMP" ]] && SSH+=(-o "ProxyJump=$OHRS_PROXYJUMP")
cleanup_master() { "${SSH[@]}" -O exit "$HOST" 2>/dev/null || true; }
trap cleanup_master EXIT

TARGET="all"
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        all|yuta-*) TARGET="$arg" ;;
        *) log_fail "unknown arg: $arg (want 'all', 'yuta-<name>', or --dry-run)"; exit 2 ;;
    esac
done

[[ -f "$OHRS_BIN" ]] || { log_fail "binary not found: $OHRS_BIN"; exit 2; }
LOCAL_SHA="$(sha256sum "$OHRS_BIN" | awk '{print $1}')"
log_info "binary:  $OHRS_BIN"
log_info "sha256:  $LOCAL_SHA"
log_info "version: $("$OHRS_BIN" --version 2>/dev/null || echo '?')"

if [[ "$TARGET" == "all" ]]; then
    mapfile -t YUTAS < <(list_running_yutas "$HOST")
else
    YUTAS=("$TARGET")
fi
[[ ${#YUTAS[@]} -gt 0 ]] || { log_fail "no target yutas on ${HOST}"; exit 1; }
log_info "target:  ${YUTAS[*]}"

TS="$(date +%Y%m%d-%H%M%S)"
FAILED=()
for yuta in "${YUTAS[@]}"; do
    log_info "yuta: ${yuta}"
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "[dry-run] would back up ${REMOTE} -> ${REMOTE}.bak-${TS} and install new binary on ${yuta}"
        continue
    fi
    if ! "${SSH[@]}" "$HOST" "lxc exec ${yuta} -- cp -a ${REMOTE} ${REMOTE}.bak-${TS}"; then
        log_fail "${yuta}: backup failed — skipping"
        FAILED+=("$yuta"); continue
    fi
    "${SSH[@]}" "$HOST" "lxc exec ${yuta} -- bash -c 'cat > ${REMOTE}.new'" < "$OHRS_BIN"
    remote_sha="$("${SSH[@]}" "$HOST" "lxc exec ${yuta} -- sha256sum ${REMOTE}.new | awk '{print \$1}'")"
    if [[ "$remote_sha" != "$LOCAL_SHA" ]]; then
        log_fail "${yuta}: sha mismatch (${remote_sha} != ${LOCAL_SHA}) — live binary untouched"
        "${SSH[@]}" "$HOST" "lxc exec ${yuta} -- rm -f ${REMOTE}.new" || true
        FAILED+=("$yuta"); continue
    fi
    "${SSH[@]}" "$HOST" "lxc exec ${yuta} -- bash -c 'chmod +x ${REMOTE}.new && mv ${REMOTE}.new ${REMOTE}'"
    installed_ver="$("${SSH[@]}" "$HOST" "lxc exec ${yuta} -- ${REMOTE} --version 2>/dev/null" || echo '?')"
    log_ok "${yuta}: installed (${installed_ver}); backup ${REMOTE}.bak-${TS}"
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
    log_fail "failed: ${FAILED[*]}"
    exit 1
fi
log_ok "ohrs binary pushed to: ${YUTAS[*]}"

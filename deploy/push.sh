#!/usr/bin/env bash
# =============================================================================
# deploy/push.sh — canonical code-push for Capelle yutas.
#
# Replaces the ad-hoc "tar | lxc exec cat > | extract | systemctl restart"
# incantations scattered across old docs. One entry point, one semantic:
# "take what's on my laptop, put it on <yuta-name|all>, verify it's healthy".
#
# Usage:
#   deploy/push.sh <yuta-name|all> [--component backend|frontend|both]
#                                   [--no-restart] [--dry-run]
#
# Examples:
#   deploy/push.sh all                                  # build frontend, ship both, restart
#   deploy/push.sh yuta-okkotsu --component frontend    # frontend-only, restart
#   deploy/push.sh yuta-maki-zenin --no-restart         # ship both, don't restart
#   deploy/push.sh all --dry-run                        # print plan, don't execute
#
# Behaviour (happy path, per yuta):
#   1. Push the backend and/or frontend tarball (packed once, reused N times).
#   2. MD5-verify each artifact lands intact.
#   3. Extract into a *.new sibling, rotate old dir to *.bak-<epoch>, move new in.
#   4. Restart capelle-platform.service (unless --no-restart).
#   5. Poll /api/health until 200; bail if it never comes up.
#   6. Prune old *.bak-* dirs >7d (always keep newest 3).
#
# Errors are fail-fast: set -euo pipefail everywhere, md5 mismatch aborts,
# health failure aborts with a non-zero exit. Each yuta is independent, so a
# failure on one does NOT roll back others already swapped. That's intentional:
# partial progress is recoverable (re-run push.sh), partial rollback is not.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOST="${CAPELLE_LXC_HOST:-gojo}"

# shellcheck source=lib/push-lib.sh
source "$SCRIPT_DIR/lib/push-lib.sh"

usage() {
    sed -n '2,30p' "$0"
    exit "${1:-0}"
}

# -----------------------------------------------------------------------------
# argument parsing
# -----------------------------------------------------------------------------
TARGET=""
COMPONENT="both"
NO_RESTART="0"
DRY_RUN="0"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) usage 0 ;;
        --component)
            COMPONENT="${2:-}"
            shift 2
            ;;
        --no-restart) NO_RESTART="1"; shift ;;
        --dry-run)    DRY_RUN="1";    shift ;;
        -*)
            log_fail "unknown flag: $1"
            usage 2
            ;;
        *)
            if [[ -z "$TARGET" ]]; then
                TARGET="$1"
                shift
            else
                log_fail "unexpected positional arg: $1"
                usage 2
            fi
            ;;
    esac
done

if [[ -z "$TARGET" ]]; then
    log_fail "missing target (yuta-<name> or 'all')"
    usage 2
fi

case "$COMPONENT" in
    backend|frontend|both) ;;
    *) log_fail "invalid --component: $COMPONENT (want backend|frontend|both)"; exit 2 ;;
esac

# -----------------------------------------------------------------------------
# resolve target list
# -----------------------------------------------------------------------------
if [[ "$TARGET" == "all" ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
        # Still hit lxc list — the dry-run should reflect reality.
        mapfile -t YUTAS < <(list_running_yutas "$HOST") || true
    else
        mapfile -t YUTAS < <(list_running_yutas "$HOST")
    fi
    if [[ ${#YUTAS[@]} -eq 0 ]]; then
        log_fail "no running yuta-* containers on ${HOST}"
        exit 1
    fi
else
    # Single yuta — validate the name shape.
    if [[ ! "$TARGET" =~ ^yuta-[a-z0-9-]+$ ]]; then
        log_fail "target '${TARGET}' must be 'all' or 'yuta-<name>'"
        exit 2
    fi
    YUTAS=("$TARGET")
fi

log_info "target:     ${YUTAS[*]}"
log_info "component:  ${COMPONENT}"
log_info "restart:    $([[ "$NO_RESTART" == "1" ]] && echo no || echo yes)"
log_info "dry-run:    $([[ "$DRY_RUN" == "1" ]] && echo yes || echo no)"
log_info "host:       ${HOST}"

if [[ "$DRY_RUN" == "1" ]]; then
    for y in "${YUTAS[@]}"; do
        echo "[dry-run] would push (component=${COMPONENT}) to ${y}"
        if [[ "$NO_RESTART" == "0" ]]; then
            echo "[dry-run]   and restart capelle-platform on ${y}"
        fi
    done
    exit 0
fi

# -----------------------------------------------------------------------------
# build + pack (once, reused across yutas)
# -----------------------------------------------------------------------------
if [[ "$COMPONENT" == "frontend" || "$COMPONENT" == "both" ]]; then
    build_frontend "$REPO_ROOT"
fi

WORKDIR="$(pack_tarballs "$REPO_ROOT" "$COMPONENT")"
log_info "artifacts:  ${WORKDIR}"

# Capture tarball paths (if that component is in play) for MD5 + swap.
BACKEND_TGZ=""
FRONTEND_TGZ=""
[[ "$COMPONENT" == "backend"  || "$COMPONENT" == "both" ]] && BACKEND_TGZ="$WORKDIR/backend.tgz"
[[ "$COMPONENT" == "frontend" || "$COMPONENT" == "both" ]] && FRONTEND_TGZ="$WORKDIR/frontend.tgz"

# -----------------------------------------------------------------------------
# per-yuta loop
# -----------------------------------------------------------------------------
FAILED=()
for yuta in "${YUTAS[@]}"; do
    log_info "=============================================================="
    log_info "yuta: ${yuta}"
    log_info "=============================================================="

    if [[ -n "$BACKEND_TGZ" ]]; then
        push_artifact "$HOST" "$yuta" "$BACKEND_TGZ"  /tmp/backend.tgz
        if ! verify_md5 "$HOST" "$yuta" "$BACKEND_TGZ" /tmp/backend.tgz; then
            log_fail "${yuta}: backend md5 mismatch — skipping"
            FAILED+=("$yuta")
            continue
        fi
        swap_backend "$HOST" "$yuta"
    fi

    if [[ -n "$FRONTEND_TGZ" ]]; then
        push_artifact "$HOST" "$yuta" "$FRONTEND_TGZ" /tmp/frontend.tgz
        if ! verify_md5 "$HOST" "$yuta" "$FRONTEND_TGZ" /tmp/frontend.tgz; then
            log_fail "${yuta}: frontend md5 mismatch — skipping"
            FAILED+=("$yuta")
            continue
        fi
        swap_frontend "$HOST" "$yuta"
    fi

    if [[ "$NO_RESTART" == "0" ]]; then
        restart_service "$HOST" "$yuta"
        if ! healthcheck "$HOST" "$yuta"; then
            log_fail "${yuta}: healthcheck failed after restart"
            FAILED+=("$yuta")
            continue
        fi
    else
        log_warn "  --no-restart: skipping systemctl restart + healthcheck"
    fi

    prune_backups "$HOST" "$yuta"
    log_ok "${yuta} done"
done

# -----------------------------------------------------------------------------
# summary
# -----------------------------------------------------------------------------
log_info "=============================================================="
if [[ ${#FAILED[@]} -eq 0 ]]; then
    log_ok "push complete: ${YUTAS[*]}"
    exit 0
else
    log_fail "push completed with failures on: ${FAILED[*]}"
    exit 1
fi

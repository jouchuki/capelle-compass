#!/usr/bin/env bash
# deploy/lib/push-lib.sh — helpers sourced by deploy/push.sh
#
# All functions write progress to stderr (so stdout stays clean for
# scripting). `set -euo pipefail` is enforced by the caller.

# -----------------------------------------------------------------------------
# logging helpers
# -----------------------------------------------------------------------------
_PUSH_TAG="[push]"

log_info()  { printf "%s %s\n" "$_PUSH_TAG" "$*" >&2; }
log_ok()    { printf "%s OK   %s\n" "$_PUSH_TAG" "$*" >&2; }
log_warn()  { printf "%s WARN %s\n" "$_PUSH_TAG" "$*" >&2; }
log_fail()  { printf "%s FAIL %s\n" "$_PUSH_TAG" "$*" >&2; }

# -----------------------------------------------------------------------------
# list_running_yutas — prints "yuta-*" container names that are RUNNING
# -----------------------------------------------------------------------------
list_running_yutas() {
    local host="${1:-gojo}"
    # `lxc list --format csv -c n,s` emits "name,state" per line. Filter on
    # name prefix + running state. No headers in csv mode.
    ssh "$host" "lxc list --format csv -c n,s" \
        | awk -F, '$1 ~ /^yuta-/ && $2 == "RUNNING" { print $1 }'
}

# -----------------------------------------------------------------------------
# build_frontend — compile frontend/dist from frontend/
# -----------------------------------------------------------------------------
build_frontend() {
    local repo_root="$1"
    log_info "building frontend (npm run build)..."
    (cd "$repo_root/frontend" && npm run build >&2)
    if [[ ! -d "$repo_root/frontend/dist" ]]; then
        log_fail "frontend build produced no dist/ directory"
        return 1
    fi
    log_ok "frontend build -> $repo_root/frontend/dist"
}

# -----------------------------------------------------------------------------
# pack_tarballs — create /tmp/capelle-push-<epoch>/{backend,frontend}.tgz
# Prints the working dir on stdout.
# -----------------------------------------------------------------------------
pack_tarballs() {
    local repo_root="$1"
    local components="$2"   # "backend" | "frontend" | "both"
    local stamp
    stamp="$(date +%s)"
    local workdir="/tmp/capelle-push-${stamp}"
    mkdir -p "$workdir"

    if [[ "$components" == "backend" || "$components" == "both" ]]; then
        log_info "packing backend -> $workdir/backend.tgz"
        # Exclude caches; we want the on-yuta layout to match /opt/backend/capelle_platform.
        tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
            -C "$repo_root/backend" -czf "$workdir/backend.tgz" capelle_platform
    fi
    if [[ "$components" == "frontend" || "$components" == "both" ]]; then
        log_info "packing frontend -> $workdir/frontend.tgz"
        tar -C "$repo_root/frontend" -czf "$workdir/frontend.tgz" dist
    fi
    echo "$workdir"
}

# -----------------------------------------------------------------------------
# push_artifact — stream a tarball into a yuta via `lxc exec ... cat > ...`
#
# We intentionally DO NOT use `lxc file push`: INFRA_AUDIT flagged that it
# returns "Forbidden" on some configurations (likely a remote-vs-local permission
# mismatch on the LXD daemon's unix socket). Streaming via `lxc exec` uses the
# same path the interactive shell uses and has been reliable.
# -----------------------------------------------------------------------------
push_artifact() {
    local host="$1" yuta="$2" local_path="$3" remote_path="$4"
    log_info "  push -> ${yuta}:${remote_path}"
    ssh "$host" "lxc exec ${yuta} -- bash -c 'cat > ${remote_path}'" < "$local_path"
}

# -----------------------------------------------------------------------------
# verify_md5 — compare local MD5 with MD5 of the file inside the container.
# Fails (non-zero) on mismatch. Never proceed to extract-and-swap on mismatch.
# -----------------------------------------------------------------------------
verify_md5() {
    local host="$1" yuta="$2" local_path="$3" remote_path="$4"
    local local_md5 remote_md5
    local_md5="$(md5sum "$local_path" | awk '{print $1}')"
    remote_md5="$(ssh "$host" "lxc exec ${yuta} -- md5sum ${remote_path}" | awk '{print $1}')"
    if [[ "$local_md5" != "$remote_md5" ]]; then
        log_fail "  md5 mismatch on ${yuta}:${remote_path} (local=${local_md5} remote=${remote_md5})"
        return 1
    fi
    log_ok "  md5 verified ${yuta}:${remote_path} (${local_md5:0:8}...)"
}

# -----------------------------------------------------------------------------
# swap_backend / swap_frontend — atomically move the new tree into place
# Keeps a dated backup (dist.bak-<epoch> / capelle_platform.bak-<epoch>).
# -----------------------------------------------------------------------------
swap_backend() {
    local host="$1" yuta="$2"
    local stamp
    stamp="$(date +%s)"
    log_info "  extract + swap backend on ${yuta}..."
    ssh "$host" "lxc exec ${yuta} -- bash -s" <<REMOTE
set -euo pipefail
cd /opt/backend
rm -rf capelle_platform.new
mkdir capelle_platform.new
tar -xzf /tmp/backend.tgz -C capelle_platform.new --strip-components=1
if [[ -d capelle_platform ]]; then
    mv capelle_platform capelle_platform.bak-${stamp}
fi
mv capelle_platform.new capelle_platform
rm -f /tmp/backend.tgz
REMOTE
    log_ok "  backend swapped on ${yuta} (previous -> capelle_platform.bak-${stamp})"
}

swap_frontend() {
    local host="$1" yuta="$2"
    local stamp
    stamp="$(date +%s)"
    log_info "  extract + swap frontend on ${yuta}..."
    ssh "$host" "lxc exec ${yuta} -- bash -s" <<REMOTE
set -euo pipefail
cd /opt/frontend
rm -rf dist.new
mkdir dist.new
tar -xzf /tmp/frontend.tgz -C dist.new --strip-components=1
if [[ -d dist ]]; then
    mv dist dist.bak-${stamp}
fi
mv dist.new dist
rm -f /tmp/frontend.tgz
REMOTE
    log_ok "  frontend swapped on ${yuta} (previous -> dist.bak-${stamp})"
}

# -----------------------------------------------------------------------------
# restart_service — restart capelle-platform.service on a yuta
# -----------------------------------------------------------------------------
restart_service() {
    local host="$1" yuta="$2"
    log_info "  restart capelle-platform on ${yuta}..."
    ssh "$host" "lxc exec ${yuta} -- systemctl restart capelle-platform"
}

# -----------------------------------------------------------------------------
# healthcheck — poll /api/health inside the yuta (localhost 8080) until 200 or timeout.
# Returns 0 on success, 1 on timeout.
# -----------------------------------------------------------------------------
healthcheck() {
    local host="$1" yuta="$2"
    local attempts="${3:-30}" sleep_s="${4:-2}"
    local status=""
    for i in $(seq 1 "$attempts"); do
        status="$(ssh "$host" "lxc exec ${yuta} -- curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/health" 2>/dev/null || echo "000")"
        if [[ "$status" == "200" ]]; then
            log_ok "  health ${yuta} /api/health -> 200 (${i}x${sleep_s}s)"
            return 0
        fi
        sleep "$sleep_s"
    done
    log_fail "  health ${yuta} /api/health never reached 200 (last=${status})"
    return 1
}

# -----------------------------------------------------------------------------
# prune_backups — delete *.bak-* older than 7 days, always keep the most
# recent 3. Runs after a successful deploy; failures here are non-fatal.
# -----------------------------------------------------------------------------
prune_backups() {
    local host="$1" yuta="$2"
    log_info "  prune old backups on ${yuta}..."
    ssh "$host" "lxc exec ${yuta} -- bash -s" <<'REMOTE' || log_warn "  prune step failed (non-fatal)"
set -euo pipefail
prune_dir() {
    local base="$1" prefix="$2"
    [[ -d "$base" ]] || return 0
    cd "$base"
    # List matching dirs, newest first by mtime.
    mapfile -t dirs < <(ls -1dt ${prefix}.bak-* 2>/dev/null || true)
    local count=${#dirs[@]}
    [[ $count -le 3 ]] && return 0
    local cutoff
    cutoff=$(( $(date +%s) - 7*24*3600 ))
    # Always keep the newest 3; prune older-than-7-days from the rest.
    for (( i=3; i<count; i++ )); do
        local d="${dirs[$i]}"
        local mt
        mt=$(stat -c %Y "$d")
        if (( mt < cutoff )); then
            echo "[prune] rm -rf $base/$d"
            rm -rf "$d"
        fi
    done
}
prune_dir /opt/backend capelle_platform
prune_dir /opt/frontend dist
REMOTE
}

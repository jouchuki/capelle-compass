#!/usr/bin/env bash
# =============================================================================
# deploy/bootstrap-yuta.sh — spin up a brand-new yuta LXC from scratch.
#
# Usage:
#   deploy/bootstrap-yuta.sh [--name yuta-<char>] [--image ubuntu:24.04]
#                             [--dry-run]
#
# What it does (high level):
#   1. Pick a name (from the JJK pool) or accept --name; refuse to overwrite.
#   2. `lxc launch` on gojo with the given image (default: matches yuta-okkotsu).
#   3. Wait for network.
#   4. apt install baseline (python, node, curl, jq, tailscale, snapd).
#   5. Copy /usr/local/bin/ohrs from yuta-okkotsu (same sha as production).
#   6. snap install codex (every yuta has its own login now — no owner/follower).
#   7. Apply tailscale up (if CAPELLE_TAILSCALE_AUTHKEY present).
#   8. Create /opt/backend, /opt/frontend, /opt/capelle-repo-agent skeletons.
#   9. Install /etc/capelle-codex.env from the template.
#  10. Install /etc/systemd/system/capelle-platform.service from the template.
#  11. Deploy current code via deploy/push.sh <name> --no-restart
#      (we skip restart here — first restart happens after the operator runs
#      `codex login --device-auth` and the sync timer fills the env file.)
#  12. Install the codex-sync timer.
#  13. Print "next steps" — always: `codex login --device-auth` on this yuta.
#
# Idempotency: refuses to continue if the target container already exists.
# Dry-run: skips ALL mutating operations (including `lxc launch`). Safe to run.
#
# This script coordinates with the TOKEN-SYNC agent. It intentionally:
#   - does NOT write /etc/capelle-codex.env's CODEX_* values (sync timer does)
#   - does NOT install the codex-sync timer if the installer script is missing
#     (warns and skips; user can re-run TOKEN-SYNC's installer later)
#
# This script relies on deploy/push.sh for the code delivery step. If push.sh
# fails, the container is left half-built — delete it with
# `ssh gojo "lxc delete -f <name>"` and re-run.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${CAPELLE_LXC_HOST:-gojo}"
PUSH_SCRIPT="$SCRIPT_DIR/push.sh"
TEMPLATE_DIR="$SCRIPT_DIR/templates"
CODEX_SYNC_INSTALLER="$SCRIPT_DIR/scripts/install-codex-sync.sh"

TAG="[bootstrap]"

log()     { printf "%s %s\n" "$TAG" "$*" >&2; }
log_ok()  { printf "%s OK   %s\n" "$TAG" "$*" >&2; }
log_warn(){ printf "%s WARN %s\n" "$TAG" "$*" >&2; }
log_fail(){ printf "%s FAIL %s\n" "$TAG" "$*" >&2; }

# JJK pool — fixed order, pick first free. Intentionally omits yuta-okkotsu
# and yuta-maki-zenin (already exist).
JJK_POOL=(
    yuta-sukuna yuta-megumi yuta-nanami yuta-toji yuta-geto
    yuta-hakari yuta-yuki yuta-nobara yuta-inumaki yuta-choso
    yuta-kashimo yuta-higuruma
)

usage() {
    sed -n '2,30p' "$0"
    exit "${1:-0}"
}

# -----------------------------------------------------------------------------
# arg parsing
# -----------------------------------------------------------------------------
NAME=""
IMAGE="ubuntu:24.04"   # matches current yutas (INFRA §5 said 22.04 but both live yutas are noble)
DRY_RUN="0"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) usage 0 ;;
        --name)    NAME="${2:-}";    shift 2 ;;
        --image)   IMAGE="${2:-}";   shift 2 ;;
        --role)    shift 2 ;;  # ignored — architecture is per-yuta independent now
        --dry-run) DRY_RUN="1";      shift   ;;
        *) log_fail "unknown arg: $1"; usage 2 ;;
    esac
done

# -----------------------------------------------------------------------------
# pick name from pool if --name not given
# -----------------------------------------------------------------------------
if [[ -z "$NAME" ]]; then
    log "picking first unused yuta name from JJK pool..."
    # Query existing yuta-* names.
    EXISTING="$(ssh "$HOST" "lxc list --format csv -c n" | grep -E '^yuta-' || true)"
    for candidate in "${JJK_POOL[@]}"; do
        if ! grep -qxF "$candidate" <<<"$EXISTING"; then
            NAME="$candidate"
            break
        fi
    done
    if [[ -z "$NAME" ]]; then
        log_fail "JJK pool exhausted — all ${#JJK_POOL[@]} names in use. Refusing to auto-extend."
        log_fail "Existing yuta containers:"
        sed 's/^/  /' <<<"$EXISTING" >&2
        exit 1
    fi
    log_ok "selected name: ${NAME}"
else
    if [[ ! "$NAME" =~ ^yuta-[a-z0-9-]+$ ]]; then
        log_fail "--name must match ^yuta-[a-z0-9-]+\$"
        exit 2
    fi
fi

# -----------------------------------------------------------------------------
# refuse if container already exists
# -----------------------------------------------------------------------------
if ssh "$HOST" "lxc info ${NAME} >/dev/null 2>&1"; then
    log_fail "container ${NAME} already exists on ${HOST}. Use 'lxc delete' first."
    exit 1
fi

log "plan:"
log "  name       = ${NAME}"
log "  image      = ${IMAGE}"
log "  dry-run    = ${DRY_RUN}"
log "  host       = ${HOST}"

run() {
    # Helper: log the command, execute unless DRY_RUN.
    log "exec: $*"
    if [[ "$DRY_RUN" == "1" ]]; then
        return 0
    fi
    "$@"
}

run_ssh() {
    local cmd="$1"
    log "ssh ${HOST}: ${cmd}"
    if [[ "$DRY_RUN" == "1" ]]; then
        return 0
    fi
    ssh "$HOST" "$cmd"
}

run_ssh_stdin() {
    # Runs `ssh gojo "<cmd>"` with stdin fed from the heredoc arg. Because
    # DRY_RUN needs to see the body too, we log it then pipe.
    local cmd="$1"
    local body="$2"
    log "ssh ${HOST}: ${cmd}   <<<stdin: $(wc -c <<<"$body" | awk '{print $1}') bytes>"
    if [[ "$DRY_RUN" == "1" ]]; then
        return 0
    fi
    ssh "$HOST" "$cmd" <<<"$body"
}

# -----------------------------------------------------------------------------
# 1. lxc launch
# -----------------------------------------------------------------------------
log "[1/12] lxc launch ${IMAGE} ${NAME}..."
run_ssh "lxc launch ${IMAGE} ${NAME}"

# -----------------------------------------------------------------------------
# 2. wait for network
# -----------------------------------------------------------------------------
log "[2/12] waiting for network inside ${NAME}..."
if [[ "$DRY_RUN" != "1" ]]; then
    for i in $(seq 1 30); do
        if ssh "$HOST" "lxc exec ${NAME} -- curl -sfm 5 -o /dev/null https://archive.ubuntu.com/" 2>/dev/null; then
            log_ok "  network up after ${i}s"
            break
        fi
        sleep 1
    done
fi

# -----------------------------------------------------------------------------
# 3. apt baseline
# -----------------------------------------------------------------------------
log "[3/12] installing apt baseline..."
# Per INFRA §5: python3/python3-dev, curl, git, build-essential, nodejs, cron, snapd
APT_BODY='set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    python3 python3-dev python3-venv python3-pip \
    curl jq git build-essential ca-certificates \
    nodejs npm cron snapd
# Enable snapd — noble LXC images often need a reboot for snap support, but
# the squashfs loop works without it if /dev is set up. We try snap install
# later; if it fails on a fresh container, user re-runs the step manually.
systemctl enable --now snapd.socket || true
'
run_ssh_stdin "lxc exec ${NAME} -- bash -s" "$APT_BODY"

# Tailscale needs its own apt repo on noble.
log "[4/12] installing tailscale..."
TS_BODY='set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list
apt-get update -qq
apt-get install -y -qq tailscale
'
run_ssh_stdin "lxc exec ${NAME} -- bash -s" "$TS_BODY"

# -----------------------------------------------------------------------------
# 4. copy ohrs binary from yuta-okkotsu
# -----------------------------------------------------------------------------
log "[5/12] copying /usr/local/bin/ohrs from yuta-okkotsu..."
# Stream from okkotsu through gojo into the new container. We don't trust the
# upstream source of the binary (INFRA §5: build source not on yuta), so we
# pin to whatever okkotsu has; TOKEN-SYNC pins the hash (f44cc18...).
OHRS_COPY='set -euo pipefail
lxc exec yuta-okkotsu -- cat /usr/local/bin/ohrs > /tmp/ohrs-bin
lxc exec '"${NAME}"' -- bash -c "cat > /usr/local/bin/ohrs && chmod +x /usr/local/bin/ohrs" < /tmp/ohrs-bin
rm -f /tmp/ohrs-bin
'
run_ssh_stdin "bash -s" "$OHRS_COPY"

# -----------------------------------------------------------------------------
# 5. snap install codex — every yuta has its own login now
# -----------------------------------------------------------------------------
log "[6/12] snap install codex..."
CODEX_BODY='set -euo pipefail
# snapd may need a moment on a fresh container.
snap wait system seed.loaded || true
snap install codex
'
run_ssh_stdin "lxc exec ${NAME} -- bash -s" "$CODEX_BODY"

# -----------------------------------------------------------------------------
# 6. tailscale up (skip if no auth key, do not block on interactive)
# -----------------------------------------------------------------------------
if [[ -n "${CAPELLE_TAILSCALE_AUTHKEY:-}" ]]; then
    log "[7/12] tailscale up as ${NAME}..."
    TS_UP='set -euo pipefail
systemctl enable --now tailscaled
tailscale up --authkey='"${CAPELLE_TAILSCALE_AUTHKEY}"' --hostname='"${NAME}"' --reset
'
    run_ssh_stdin "lxc exec ${NAME} -- bash -s" "$TS_UP"
else
    log_warn "[7/12] CAPELLE_TAILSCALE_AUTHKEY not set — skipping tailscale up."
    log_warn "       Run manually later: ssh ${HOST} \"lxc exec ${NAME} -- tailscale up --authkey=<key> --hostname=${NAME}\""
fi

# -----------------------------------------------------------------------------
# 7. directory skeleton + templates
# -----------------------------------------------------------------------------
log "[8/12] creating /opt tree + installing env/unit templates..."
SVC_TEMPLATE="$(cat "$TEMPLATE_DIR/capelle-platform.service")"
ENV_TEMPLATE="$(cat "$TEMPLATE_DIR/capelle-codex.env.tmpl")"
# Substitute hostname placeholder; leave secrets as-is for the operator/TOKEN-SYNC
# to fill post-bootstrap.
ENV_FILLED="$(sed "s|__HOSTNAME__|${NAME}|g" <<<"$ENV_TEMPLATE")"

SKEL_BODY='set -euo pipefail
mkdir -p /opt/backend /opt/frontend /opt/capelle-repo-agent
mkdir -p /opt/capelle-repo-agent/.openharnessrs/plugins/capelle
# env file (0600, root)
umask 077
cat > /etc/capelle-codex.env <<'"'"'ENVEOF'"'"'
'"${ENV_FILLED}"'
ENVEOF
chmod 600 /etc/capelle-codex.env
# systemd unit
cat > /etc/systemd/system/capelle-platform.service <<'"'"'SVCEOF'"'"'
'"${SVC_TEMPLATE}"'
SVCEOF
systemctl daemon-reload
systemctl enable capelle-platform.service
'
run_ssh_stdin "lxc exec ${NAME} -- bash -s" "$SKEL_BODY"

# -----------------------------------------------------------------------------
# 8. deploy current code via push.sh (no restart — env has no tokens yet)
# -----------------------------------------------------------------------------
log "[9/12] deploying current code via push.sh ${NAME} --no-restart..."
if [[ "$DRY_RUN" == "1" ]]; then
    log "       (dry-run — skipping push.sh invocation)"
else
    if ! "$PUSH_SCRIPT" "$NAME" --no-restart; then
        log_fail "push.sh failed — ${NAME} is half-built. Delete with 'lxc delete -f ${NAME}' and retry."
        exit 1
    fi
fi

# -----------------------------------------------------------------------------
# 9. install TOKEN-SYNC timer (gated — don't fail if agent hasn't landed yet)
# -----------------------------------------------------------------------------
log "[10/12] installing codex-sync timer..."
# Installer is designed to run INSIDE the target container —
# it's fed over stdin to `lxc exec <yuta> -- bash -s`. We stream it.
if [[ -f "$CODEX_SYNC_INSTALLER" ]]; then
    log "  streaming ${CODEX_SYNC_INSTALLER} into ${NAME}..."
    if [[ "$DRY_RUN" != "1" ]]; then
        if ! ssh "$HOST" "lxc exec ${NAME} -- bash -s" < "$CODEX_SYNC_INSTALLER"; then
            log_warn "  install-codex-sync.sh returned non-zero — expected when 'codex login' hasn't run yet."
        fi
    fi
else
    log_warn "  ${CODEX_SYNC_INSTALLER} not found — skipping."
    log_warn "  Re-run this step manually:"
    log_warn "    ssh ${HOST} \"lxc exec ${NAME} -- bash -s\" < deploy/scripts/install-codex-sync.sh"
fi

# -----------------------------------------------------------------------------
# 10. final message
# -----------------------------------------------------------------------------
log "[11/12] bootstrap finished."
log "[12/12] NEXT STEPS:"
cat >&2 <<EOF
  * Run the one-time Codex device auth on THIS yuta (per-yuta independent):
      ssh ${HOST} "lxc exec ${NAME} -- snap run codex login --device-auth"
    Follow the URL, paste the code. After that, the snap holds valid tokens
    that THIS yuta's codex-sync timer copies into /etc/capelle-codex.env.
  * Verify /etc/capelle-codex.env populates within ~5 min (first timer tick):
      ssh ${HOST} "lxc exec ${NAME} -- cat /etc/capelle-codex.env" | grep CODEX_ACCESS_TOKEN
  * Start the service once tokens are in the env file:
      ssh ${HOST} "lxc exec ${NAME} -- systemctl start capelle-platform"
  * Add to nginx LB (masamichi-yaga):
      ssh ${HOST} "lxc exec masamichi-yaga -- bash -c 'echo \"    server ${NAME}:8080 max_fails=3 fail_timeout=30s;\" >> /etc/nginx/conf.d/capelle.conf && nginx -t && systemctl reload nginx'"
    (or use scripts/add-to-lb.sh)
EOF
fi
log_ok "done."

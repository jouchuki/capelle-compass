#!/usr/bin/env bash
# =============================================================================
# scripts/deploy-v1.1.sh
# -----------------------------------------------------------------------------
# Purpose:
#   End-to-end orchestrator for the Capelle Platform v1.1 cutover. Chains the
#   individual provisioners (aoi-todo, kiyotaka-ijichi, yuji-itadori,
#   masamichi-yaga), runs the SQLite->Postgres + Chroma migrations, rewrites
#   /etc/capelle-codex.env on yuta-okkotsu, restarts the service, and (on
#   explicit opt-in) flips the Cloudflare Tunnel onto masamichi-yaga.
#
#   The cutover is the one-way door. Default mode is --dry-run. The tunnel
#   flip is gated on --execute AND --yes — belt-and-braces.
#
# Runs on:
#   The workstation. Every step either SSHes into `gojo` itself or delegates
#   to a per-component provisioner that does. This script does not run inside
#   any container.
#
# Invocation:
#   ./scripts/deploy-v1.1.sh --dry-run
#   ./scripts/deploy-v1.1.sh --execute
#   ./scripts/deploy-v1.1.sh --execute --skip-chroma
#   ./scripts/deploy-v1.1.sh --execute --skip-cutover
#   ./scripts/deploy-v1.1.sh --execute --yes           # permit cutover
#
# Required env (checked in step 1):
#   TAILSCALE_AUTHKEY          Reusable Tailscale auth key.
#   CAPELLE_PG_PASSWORD        Password for the Postgres `capelle` role.
#   CAPELLE_RABBITMQ_PASSWORD  Password for the RabbitMQ `capelle` user.
#   CAPELLE_JWT_SECRET         Shared JWT secret across all yutas.
#
# Optional env:
#   CAPELLE_PG_DB              Postgres database name. Default: capelle.
#   CAPELLE_PG_USER            Postgres role name. Default: capelle.
#   CAPELLE_RMQ_USER           RabbitMQ username. Default: capelle.
#   CAPELLE_RMQ_VHOST          RabbitMQ vhost. Default: capelle.
#   TEMPLATE_CONTAINER         The live yuta to migrate. Default: yuta-okkotsu.
#   SQLITE_PATH_IN_CONTAINER   Default: /opt/backend/capelle_platform.db.
#   HEALTH_WAIT_SECS           Max wait for /api/health post-restart. Default 120.
#
# Idempotency contract:
#   Every step has a CHEAP PROBE that decides skip (already done) vs execute.
#   Probes never mutate state. Re-running the orchestrator is safe; the only
#   step that cannot be trivially reversed is the cutover (step 11) and it is
#   protected by a second --yes flag.
#
# Logging:
#   Every line is prefixed `[v1.1][step-N] <timestamp>`. Tee'd to
#   /var/log/capelle-v1.1-deploy.log on the control host.
# =============================================================================

set -euo pipefail

TAG="[v1.1]"
HOST="${HOST:-gojo}"
LOG_FILE="/var/log/capelle-v1.1-deploy.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Globals for step tracking (used by ERR trap).
STEP="init"

# Defaults that can be overridden via env.
TEMPLATE_CONTAINER="${TEMPLATE_CONTAINER:-yuta-okkotsu}"
LB_CONTAINER="${LB_CONTAINER:-masamichi-yaga}"
PG_CONTAINER="${PG_CONTAINER:-aoi-todo}"
RMQ_CONTAINER="${RMQ_CONTAINER:-kiyotaka-ijichi}"
CHROMA_CONTAINER="${CHROMA_CONTAINER:-yuji-itadori}"

CAPELLE_PG_DB="${CAPELLE_PG_DB:-capelle}"
CAPELLE_PG_USER="${CAPELLE_PG_USER:-capelle}"
CAPELLE_RMQ_USER="${CAPELLE_RMQ_USER:-capelle}"
CAPELLE_RMQ_VHOST="${CAPELLE_RMQ_VHOST:-capelle}"

SQLITE_PATH_IN_CONTAINER="${SQLITE_PATH_IN_CONTAINER:-/opt/backend/capelle_platform.db}"
HEALTH_WAIT_SECS="${HEALTH_WAIT_SECS:-120}"

BACKUP_DIR="${REPO_ROOT}/backups"

# ---- arg parsing ----------------------------------------------------------
DRY_RUN=""
EXECUTE=""
SKIP_CHROMA=0
SKIP_CUTOVER=0
CUTOVER_YES=0

for arg in "$@"; do
    case "$arg" in
        --dry-run)      DRY_RUN=1 ;;
        --execute)      EXECUTE=1 ;;
        --skip-chroma)  SKIP_CHROMA=1 ;;
        --skip-cutover) SKIP_CUTOVER=1 ;;
        --yes)          CUTOVER_YES=1 ;;
        -h|--help)
            sed -n '2,80p' "$0"
            exit 0
            ;;
        *)
            echo "$TAG unknown flag: ${arg}" >&2
            echo "$TAG see: $0 --help" >&2
            exit 2
            ;;
    esac
done

if [[ -z "$DRY_RUN" && -z "$EXECUTE" ]]; then
    echo "$TAG FATAL: pick --dry-run or --execute explicitly" >&2
    exit 2
fi
if [[ -n "$DRY_RUN" && -n "$EXECUTE" ]]; then
    echo "$TAG FATAL: --dry-run and --execute are mutually exclusive" >&2
    exit 2
fi

# Normalise for gating. Using "0" / "1" strings so [[ "$DRY_RUN" = "0" ]] reads
# naturally in every destructive site.
if [[ -n "$EXECUTE" ]]; then
    DRY_RUN=0
else
    DRY_RUN=1
fi

# ---- logging --------------------------------------------------------------
# Tee every line to the log. The log dir exists on any control host; if not,
# fall back to the current directory to avoid silent loss.
if [[ ! -w "$(dirname "$LOG_FILE")" ]]; then
    LOG_FILE="${REPO_ROOT}/capelle-v1.1-deploy.log"
fi
# `exec > >(tee -a ...)` tee's stdout AND stderr in a way that survives the
# ERR trap (stderr goes to both log and terminal).
exec > >(tee -a "$LOG_FILE") 2>&1

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() {
    # log <step-label> <msg>
    local step="$1"; shift
    echo "${TAG}[${step}] $(ts) $*"
}

# ---- failure reporting ----------------------------------------------------
trap 'echo "${TAG} FAILED at step ${STEP} (see ${LOG_FILE})" >&2' ERR

# ---- helpers --------------------------------------------------------------
container_exists() {
    # cheap idempotency probe — non-mutating
    local name="$1"
    ssh "$HOST" "lxc list --format csv -c n | grep -qx '${name}'"
}

container_ip() {
    local name="$1"
    ssh "$HOST" "lxc exec ${name} -- tailscale ip -4 2>/dev/null | head -n1" || true
}

service_active_in_container() {
    local container="$1" unit="$2"
    ssh "$HOST" "lxc exec ${container} -- systemctl is-active ${unit} 2>/dev/null" | grep -qx "active"
}

maybe_run() {
    # Wrap any shell invocation so --dry-run short-circuits before side effects.
    # Usage: maybe_run <description> <command...>
    local desc="$1"; shift
    if [[ "$DRY_RUN" = "1" ]]; then
        log "$STEP" "DRY-RUN would: ${desc}"
        return 0
    fi
    log "$STEP" "EXEC: ${desc}"
    "$@"
}

redact_dsn() {
    # Redact password portion of a DSN-like URL for safe logging.
    local url="$1"
    # Match <scheme>://<user>:<pw>@<host...>
    # POSIX-portable; avoids printing secrets in audit trails.
    python3 - "$url" <<'PY' 2>/dev/null || echo "<unprintable>"
import re, sys
u = sys.argv[1]
m = re.match(r'(?P<head>[a-z+]+://)(?P<user>[^:@/]+)(?::(?P<pw>[^@]+))?@(?P<rest>.*)', u)
if not m:
    print(u)
else:
    pw = m.group('pw')
    if pw is None:
        print(u)
    else:
        print(f"{m.group('head')}{m.group('user')}:***@{m.group('rest')}")
PY
}

# ==========================================================================
# step 1: pre-flight
# ==========================================================================
STEP="step-1"
log "$STEP" "pre-flight: verifying env + SSH + LXC on ${HOST}"

missing_env=()
for v in TAILSCALE_AUTHKEY CAPELLE_PG_PASSWORD CAPELLE_RABBITMQ_PASSWORD CAPELLE_JWT_SECRET; do
    if [[ -z "${!v:-}" ]]; then
        missing_env+=("$v")
    fi
done
if (( ${#missing_env[@]} > 0 )); then
    echo "$TAG FATAL: missing env vars: ${missing_env[*]}" >&2
    exit 1
fi
log "$STEP" "env vars present (values redacted)"

if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$HOST" "true" 2>/dev/null; then
    echo "$TAG FATAL: cannot ssh to ${HOST} non-interactively" >&2
    exit 1
fi
log "$STEP" "ssh ${HOST} OK"

if ! ssh "$HOST" "command -v lxc >/dev/null"; then
    echo "$TAG FATAL: ${HOST} has no 'lxc' binary on PATH" >&2
    exit 1
fi
if ! ssh "$HOST" "command -v tailscale >/dev/null || lxc --version >/dev/null"; then
    # tailscale on the *host* is not strictly required (containers bring their
    # own), but log its absence so the operator isn't surprised by MagicDNS.
    log "$STEP" "note: tailscale not on host PATH (OK — containers run their own)"
fi
log "$STEP" "lxc + tailscale tooling reachable"

# DSNs we build once and reuse later. Never echoed in plaintext.
POSTGRES_DSN="postgresql://${CAPELLE_PG_USER}:${CAPELLE_PG_PASSWORD}@${PG_CONTAINER}:5432/${CAPELLE_PG_DB}"
RABBITMQ_URL="amqp://${CAPELLE_RMQ_USER}:${CAPELLE_RABBITMQ_PASSWORD}@${RMQ_CONTAINER}:5672/${CAPELLE_RMQ_VHOST}"
# bare host:port — the capelle-beleid / cbs / capelle-budget CLIs parse
# this themselves and will crash with an http:// prefix
# ("invalid literal for int: '//yuji-itadori:8000'").
CHROMA_HTTP="${CHROMA_CONTAINER}:8000"

log "$STEP" "target POSTGRES_DSN = $(redact_dsn "$POSTGRES_DSN")"
log "$STEP" "target RABBITMQ_URL = $(redact_dsn "$RABBITMQ_URL")"
log "$STEP" "target CHROMA_HTTP = ${CHROMA_HTTP}"

mkdir -p "$BACKUP_DIR"

# ==========================================================================
# step 2: provision aoi-todo (Postgres)
# ==========================================================================
STEP="step-2"
log "$STEP" "provision ${PG_CONTAINER} (Postgres)"
if container_exists "$PG_CONTAINER"; then
    log "$STEP" "SKIP — ${PG_CONTAINER} already exists"
else
    maybe_run "CAPELLE_PG_PASSWORD=*** TAILSCALE_AUTHKEY=*** bash ${SCRIPT_DIR}/provision-aoi-todo.sh" \
        bash "${SCRIPT_DIR}/provision-aoi-todo.sh"
fi

if [[ "$DRY_RUN" = "0" ]]; then
    PG_IP="$(container_ip "$PG_CONTAINER")"
    log "$STEP" "${PG_CONTAINER} tailnet IP: ${PG_IP:-<unknown>}"
fi

# ==========================================================================
# step 3: provision kiyotaka-ijichi (RabbitMQ)
# ==========================================================================
STEP="step-3"
log "$STEP" "provision ${RMQ_CONTAINER} (RabbitMQ)"
if container_exists "$RMQ_CONTAINER"; then
    log "$STEP" "SKIP — ${RMQ_CONTAINER} already exists"
else
    maybe_run "CAPELLE_RABBITMQ_PASSWORD=*** TAILSCALE_AUTHKEY=*** bash ${SCRIPT_DIR}/provision-kiyotaka-ijichi.sh" \
        bash "${SCRIPT_DIR}/provision-kiyotaka-ijichi.sh"
fi

if [[ "$DRY_RUN" = "0" ]]; then
    RMQ_IP="$(container_ip "$RMQ_CONTAINER")"
    log "$STEP" "${RMQ_CONTAINER} tailnet IP: ${RMQ_IP:-<unknown>}"
fi

# ==========================================================================
# step 4: provision yuji-itadori (Chroma) — optional
# ==========================================================================
STEP="step-4"
if [[ "$SKIP_CHROMA" = "1" ]]; then
    log "$STEP" "SKIP — --skip-chroma was passed; Chroma stays on ${TEMPLATE_CONTAINER}"
else
    log "$STEP" "provision ${CHROMA_CONTAINER} (Chroma)"
    CHROMA_PROVISIONER="${SCRIPT_DIR}/provision-yuji-itadori.sh"
    if container_exists "$CHROMA_CONTAINER"; then
        log "$STEP" "SKIP — ${CHROMA_CONTAINER} already exists"
    elif [[ -x "$CHROMA_PROVISIONER" || -f "$CHROMA_PROVISIONER" ]]; then
        maybe_run "TAILSCALE_AUTHKEY=*** bash ${CHROMA_PROVISIONER}" \
            bash "$CHROMA_PROVISIONER"
    else
        log "$STEP" "WARN — ${CHROMA_PROVISIONER} not present; re-run with --skip-chroma if Agent 3 hasn't landed"
        echo "$TAG FATAL: chroma provisioner missing and --skip-chroma not set" >&2
        exit 1
    fi

    if [[ "$DRY_RUN" = "0" ]]; then
        CHROMA_IP="$(container_ip "$CHROMA_CONTAINER")"
        log "$STEP" "${CHROMA_CONTAINER} tailnet IP: ${CHROMA_IP:-<unknown>}"
    fi
fi

# ==========================================================================
# step 5: migrate Chroma from ${TEMPLATE_CONTAINER} -> yuji-itadori
# ==========================================================================
STEP="step-5"
if [[ "$SKIP_CHROMA" = "1" ]]; then
    log "$STEP" "SKIP — --skip-chroma was passed"
else
    log "$STEP" "migrate Chroma data to ${CHROMA_CONTAINER}"
    CHROMA_MIGRATOR="${SCRIPT_DIR}/migrate-chroma.sh"

    # Probe: both sides reachable AND the destination heartbeat reports data.
    # Best-effort only — if curl isn't installed we fall through to execute.
    chroma_migrated=0
    if [[ "$DRY_RUN" = "0" ]] && container_exists "$CHROMA_CONTAINER"; then
        # Chroma v2 heartbeat returns ns since epoch on 200. If collections
        # exist we consider migration done.
        HB="$(ssh "$HOST" "lxc exec ${CHROMA_CONTAINER} -- curl -sf http://127.0.0.1:8000/api/v2/heartbeat 2>/dev/null" || true)"
        # Heartbeat alone doesn't confirm data, but combined with a non-empty
        # /chroma data dir size, it's a reasonable idempotency check.
        SIZE_KB="$(ssh "$HOST" "lxc exec ${CHROMA_CONTAINER} -- du -sk /var/lib/chroma 2>/dev/null | awk '{print \$1}'" || echo 0)"
        if [[ -n "$HB" ]] && [[ "${SIZE_KB:-0}" -gt 128 ]]; then
            chroma_migrated=1
        fi
    fi

    if [[ "$chroma_migrated" = "1" ]]; then
        log "$STEP" "SKIP — ${CHROMA_CONTAINER} heartbeat OK and data dir non-empty (>${SIZE_KB}KB)"
    elif [[ -f "$CHROMA_MIGRATOR" ]]; then
        maybe_run "bash ${CHROMA_MIGRATOR}" bash "$CHROMA_MIGRATOR"
    else
        log "$STEP" "WARN — ${CHROMA_MIGRATOR} not present (Agent 3 hasn't landed)"
        echo "$TAG FATAL: chroma migrator missing and --skip-chroma not set" >&2
        exit 1
    fi
fi

# ==========================================================================
# step 6: provision masamichi-yaga (nginx LB)
# ==========================================================================
STEP="step-6"
log "$STEP" "provision ${LB_CONTAINER} (nginx LB) seeded with ${TEMPLATE_CONTAINER}"
if container_exists "$LB_CONTAINER"; then
    log "$STEP" "SKIP — ${LB_CONTAINER} already exists"
else
    maybe_run "YUTA_UPSTREAMS=${TEMPLATE_CONTAINER} TAILSCALE_AUTHKEY=*** bash ${SCRIPT_DIR}/provision-masamichi-yaga.sh" \
        env YUTA_UPSTREAMS="${TEMPLATE_CONTAINER}" bash "${SCRIPT_DIR}/provision-masamichi-yaga.sh"
fi

if [[ "$DRY_RUN" = "0" ]]; then
    LB_IP="$(container_ip "$LB_CONTAINER")"
    log "$STEP" "${LB_CONTAINER} tailnet IP: ${LB_IP:-<unknown>}"
fi

# ==========================================================================
# step 7: belt-and-braces SQLite backup from ${TEMPLATE_CONTAINER}
# ==========================================================================
STEP="step-7"
log "$STEP" "back up ${TEMPLATE_CONTAINER}'s SQLite to ${BACKUP_DIR}"

BACKUP_STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_LOCAL="${BACKUP_DIR}/${BACKUP_STAMP}-sqlite.db"
BACKUP_LOCAL_MARKER="${BACKUP_DIR}/${BACKUP_STAMP}-sqlite.db.ok"

# Probe: if a recent-enough backup marker exists (same day), skip.
# We never reuse a stale backup to gate a fresh migration — only the
# backup step itself is skipped if already done today.
TODAY="$(date +%Y%m%d)"
recent_backup="$(ls "${BACKUP_DIR}"/${TODAY}-*-sqlite.db.ok 2>/dev/null | head -n1 || true)"
if [[ -n "$recent_backup" ]]; then
    log "$STEP" "SKIP — found recent backup marker ${recent_backup}"
else
    if [[ "$DRY_RUN" = "1" ]]; then
        log "$STEP" "DRY-RUN would: lxc exec ${TEMPLATE_CONTAINER} -- sqlite3 ${SQLITE_PATH_IN_CONTAINER} '.backup /tmp/capelle-pre-v1.1.db'"
        log "$STEP" "DRY-RUN would: lxc file pull ${TEMPLATE_CONTAINER}/tmp/capelle-pre-v1.1.db ${BACKUP_LOCAL}"
    else
        ssh "$HOST" "lxc exec ${TEMPLATE_CONTAINER} -- sqlite3 ${SQLITE_PATH_IN_CONTAINER} '.backup /tmp/capelle-pre-v1.1.db'"
        ssh "$HOST" "lxc file pull ${TEMPLATE_CONTAINER}/tmp/capelle-pre-v1.1.db /tmp/capelle-pre-v1.1.db"
        scp -q "${HOST}:/tmp/capelle-pre-v1.1.db" "$BACKUP_LOCAL"
        ssh "$HOST" "rm -f /tmp/capelle-pre-v1.1.db"
        ssh "$HOST" "lxc exec ${TEMPLATE_CONTAINER} -- rm -f /tmp/capelle-pre-v1.1.db"
        touch "$BACKUP_LOCAL_MARKER"
        log "$STEP" "backup saved: $(ls -l "$BACKUP_LOCAL" | awk '{print $5, $NF}')"
    fi
fi

# ==========================================================================
# step 8: run migrate_sqlite_to_postgres.py
# ==========================================================================
STEP="step-8"
log "$STEP" "migrate data: SQLite (${TEMPLATE_CONTAINER}) -> Postgres (${PG_CONTAINER})"

# Probe: if the postgres users table has the same count as the sqlite one,
# we consider the migration done. Not perfect, but cheap and the migrator
# itself is ON CONFLICT DO NOTHING so re-running is safe anyway.
migration_done=0
if [[ "$DRY_RUN" = "0" ]] && container_exists "$PG_CONTAINER"; then
    SQLITE_USERS="$(ssh "$HOST" "lxc exec ${TEMPLATE_CONTAINER} -- sqlite3 ${SQLITE_PATH_IN_CONTAINER} 'SELECT count(*) FROM users' 2>/dev/null" || echo 0)"
    PG_USERS="$(ssh "$HOST" "lxc exec ${PG_CONTAINER} -- sudo -u postgres psql -tAc 'SELECT count(*) FROM users' ${CAPELLE_PG_DB} 2>/dev/null" || echo 0)"
    # Strip whitespace from both sides; comparison must tolerate empties.
    SQLITE_USERS="${SQLITE_USERS// /}"
    PG_USERS="${PG_USERS// /}"
    if [[ -n "${SQLITE_USERS:-}" ]] && [[ "${SQLITE_USERS}" = "${PG_USERS}" ]] && [[ "${SQLITE_USERS}" != "0" ]]; then
        migration_done=1
        log "$STEP" "probe: sqlite users=${SQLITE_USERS}, postgres users=${PG_USERS} -> match"
    else
        log "$STEP" "probe: sqlite users=${SQLITE_USERS:-?}, postgres users=${PG_USERS:-?} -> will run migrator"
    fi
fi

if [[ "$migration_done" = "1" ]]; then
    log "$STEP" "SKIP — row counts already match"
else
    # We run the migrator from the control host; it connects to both DBs via
    # tailnet. The SQLite source has to be readable from here, so pull a copy
    # of the live DB first and point the migrator at it. (The .backup from
    # step 7 IS that copy.)
    MIGRATOR="${SCRIPT_DIR}/migrate_sqlite_to_postgres.py"
    SRC_DB="${BACKUP_LOCAL:-$(ls "${BACKUP_DIR}"/${TODAY}-*-sqlite.db 2>/dev/null | head -n1)}"
    if [[ -z "${SRC_DB:-}" || ! -f "$SRC_DB" ]]; then
        # Regenerate a fresh dump if step 7 decided to skip.
        if [[ "$DRY_RUN" = "0" ]]; then
            ssh "$HOST" "lxc exec ${TEMPLATE_CONTAINER} -- sqlite3 ${SQLITE_PATH_IN_CONTAINER} '.backup /tmp/capelle-migrate.db'"
            ssh "$HOST" "lxc file pull ${TEMPLATE_CONTAINER}/tmp/capelle-migrate.db /tmp/capelle-migrate.db"
            scp -q "${HOST}:/tmp/capelle-migrate.db" "${BACKUP_DIR}/${BACKUP_STAMP}-migrate.db"
            SRC_DB="${BACKUP_DIR}/${BACKUP_STAMP}-migrate.db"
            ssh "$HOST" "rm -f /tmp/capelle-migrate.db"
            ssh "$HOST" "lxc exec ${TEMPLATE_CONTAINER} -- rm -f /tmp/capelle-migrate.db"
        else
            SRC_DB="<backup-from-step-7>"
        fi
    fi

    # The migrator imports aiosqlite + asyncpg. Prefer the backend
    # venv's python because it already has both installed; fall back
    # to system python3 when the venv isn't available on the control
    # host.
    MIGRATOR_PY="${REPO_ROOT}/backend/.venv/bin/python3"
    [[ -x "$MIGRATOR_PY" ]] || MIGRATOR_PY="python3"
    maybe_run "CAPELLE_POSTGRES_DSN=*** ${MIGRATOR_PY} ${MIGRATOR} ${SRC_DB}" \
        env CAPELLE_POSTGRES_DSN="$POSTGRES_DSN" "$MIGRATOR_PY" "$MIGRATOR" "$SRC_DB"
fi

# ==========================================================================
# step 9: rewrite /etc/capelle-codex.env on ${TEMPLATE_CONTAINER}
# ==========================================================================
STEP="step-9"
log "$STEP" "rewrite /etc/capelle-codex.env on ${TEMPLATE_CONTAINER} with v1.1 keys"

# Resolve masamichi-yaga's tailnet IP for CAPELLE_TRUSTED_PROXIES. In dry-run
# we may not have LB_IP; still print the intent.
if [[ "$DRY_RUN" = "0" ]] && [[ -z "${LB_IP:-}" ]]; then
    LB_IP="$(container_ip "$LB_CONTAINER")"
fi
LB_IP="${LB_IP:-<masamichi-yaga-tailnet-ip>}"

# Probe: is CAPELLE_STORE=postgres already in the env file?
env_rewritten=0
if [[ "$DRY_RUN" = "0" ]] && container_exists "$TEMPLATE_CONTAINER"; then
    if ssh "$HOST" "lxc exec ${TEMPLATE_CONTAINER} -- grep -qE '^CAPELLE_STORE=postgres' /etc/capelle-codex.env 2>/dev/null"; then
        env_rewritten=1
    fi
fi

if [[ "$env_rewritten" = "1" ]]; then
    log "$STEP" "SKIP — CAPELLE_STORE=postgres already present in env file"
else
    if [[ "$DRY_RUN" = "1" ]]; then
        log "$STEP" "DRY-RUN would: append/update CAPELLE_STORE, CAPELLE_POSTGRES_DSN(***), CAPELLE_QUEUE, CAPELLE_WS_HUB, CAPELLE_RABBITMQ_URL(***), CAPELLE_CHROMA_HTTP, CAPELLE_TRUSTED_PROXIES on ${TEMPLATE_CONTAINER}"
    else
        log "$STEP" "writing env file (secrets are piped via stdin, not argv)"
        # We read the existing file, drop any old instances of the keys we
        # manage, and append the v1.1 block. A hand-rolled merge beats a
        # full rewrite because other keys (OPENAI_API_KEY, CAPELLE_JWT_SECRET,
        # CAPELLE_ADMIN_EMAILS, ...) stay untouched.
        ssh "$HOST" "lxc exec ${TEMPLATE_CONTAINER} -- bash -s" <<REMOTE
set -euo pipefail
umask 077
ENV_FILE=/etc/capelle-codex.env
touch "\$ENV_FILE"
chmod 600 "\$ENV_FILE"

# Back up once per day (not per run — avoids clutter across retries).
BKP="\${ENV_FILE}.pre-v1.1.\$(date +%Y%m%d)"
if [[ ! -f "\$BKP" ]]; then
    cp -a "\$ENV_FILE" "\$BKP"
fi

# Strip keys we're about to set (tolerate absence).
grep -vE '^(CAPELLE_STORE|CAPELLE_POSTGRES_DSN|CAPELLE_QUEUE|CAPELLE_WS_HUB|CAPELLE_RABBITMQ_URL|CAPELLE_CHROMA_HTTP|CAPELLE_TRUSTED_PROXIES)=' "\$ENV_FILE" > "\${ENV_FILE}.new" || true

cat >> "\${ENV_FILE}.new" <<ENVEOF
CAPELLE_STORE=postgres
CAPELLE_POSTGRES_DSN=${POSTGRES_DSN}
CAPELLE_QUEUE=rabbitmq
CAPELLE_WS_HUB=rabbitmq
CAPELLE_RABBITMQ_URL=${RABBITMQ_URL}
CAPELLE_CHROMA_HTTP=${CHROMA_HTTP}
CAPELLE_TRUSTED_PROXIES=${LB_IP}
ENVEOF

mv "\${ENV_FILE}.new" "\$ENV_FILE"
chmod 600 "\$ENV_FILE"
# Sanity — show only the KEYS we wrote, never values.
echo "[env] keys now present:"
awk -F= '/^[A-Z_]+=/{print "  " \$1}' "\$ENV_FILE" | sort -u
REMOTE
    fi
fi

# ==========================================================================
# step 10: restart capelle-platform + wait for /api/health via LB
# ==========================================================================
STEP="step-10"
log "$STEP" "restart capelle-platform.service on ${TEMPLATE_CONTAINER} and wait for /api/health via ${LB_CONTAINER}"

if [[ "$DRY_RUN" = "1" ]]; then
    log "$STEP" "DRY-RUN would: systemctl restart capelle-platform on ${TEMPLATE_CONTAINER}"
    log "$STEP" "DRY-RUN would: poll http://${LB_CONTAINER}:8080/api/health for up to ${HEALTH_WAIT_SECS}s"
else
    ssh "$HOST" "lxc exec ${TEMPLATE_CONTAINER} -- systemctl restart capelle-platform.service"

    ATTEMPTS=$(( HEALTH_WAIT_SECS / 2 ))
    healthy=0
    for i in $(seq 1 "$ATTEMPTS"); do
        STATUS="$(ssh "$HOST" "lxc exec ${LB_CONTAINER} -- curl -sf -o /dev/null -w '%{http_code}' http://${TEMPLATE_CONTAINER}:8080/api/health" 2>/dev/null || echo 000)"
        if [[ "$STATUS" = "200" ]]; then
            healthy=1
            log "$STEP" "/api/health -> 200 after ~$(( i * 2 ))s (probed via ${LB_CONTAINER})"
            break
        fi
        sleep 2
    done
    if [[ "$healthy" = "0" ]]; then
        echo "$TAG FATAL: ${TEMPLATE_CONTAINER} did not become healthy within ${HEALTH_WAIT_SECS}s" >&2
        echo "$TAG diagnose: ssh ${HOST} 'lxc exec ${TEMPLATE_CONTAINER} -- journalctl -u capelle-platform -n 100 --no-pager'" >&2
        exit 1
    fi
fi

# ==========================================================================
# step 11: optional Cloudflare Tunnel cutover
# ==========================================================================
STEP="step-11"
if [[ "$SKIP_CUTOVER" = "1" ]]; then
    log "$STEP" "SKIP — --skip-cutover was passed"
    log "$STEP" "To flip later: bash ${SCRIPT_DIR}/cutover-tunnel.sh --yes"
elif [[ "$DRY_RUN" = "1" ]]; then
    log "$STEP" "DRY-RUN would: bash ${SCRIPT_DIR}/cutover-tunnel.sh --yes  (gated on --execute AND --yes)"
elif [[ "$CUTOVER_YES" != "1" ]]; then
    log "$STEP" "HOLD — cutover requires an explicit --yes even in --execute mode (one-way door)"
    log "$STEP" "To flip now, re-run: $0 --execute --yes   OR   bash ${SCRIPT_DIR}/cutover-tunnel.sh --yes"
else
    # Interactive double-confirm, unless stdin is not a TTY (CI) — then
    # --yes is sufficient.
    if [[ -t 0 ]]; then
        echo ""
        echo "$TAG ABOUT TO FLIP the Cloudflare Tunnel origin from ${TEMPLATE_CONTAINER} to ${LB_CONTAINER}."
        echo "$TAG This is the one-way door. Type 'CUTOVER' (uppercase) to proceed:"
        read -r answer
        if [[ "$answer" != "CUTOVER" ]]; then
            echo "$TAG aborted by operator at interactive confirmation" >&2
            exit 1
        fi
    fi
    bash "${SCRIPT_DIR}/cutover-tunnel.sh" --yes
fi

# ==========================================================================
# step 12: summary
# ==========================================================================
STEP="step-12"
log "$STEP" "summary"

summary_container() {
    local name="$1"
    if ! container_exists "$name" 2>/dev/null; then
        echo "  ${name}: NOT PRESENT"
        return
    fi
    local ip="$(container_ip "$name")"
    local state="$(ssh "$HOST" "lxc list --format csv -c s ${name}" 2>/dev/null || echo '?')"
    echo "  ${name}: state=${state}  ip=${ip:-?}"
}

if [[ "$DRY_RUN" = "1" ]]; then
    echo ""
    echo "$TAG === DRY-RUN COMPLETE ==="
    echo "$TAG Re-run with --execute to apply. Cutover requires --execute --yes."
    echo "$TAG Log: ${LOG_FILE}"
    exit 0
fi

echo ""
echo "$TAG === v1.1 DEPLOYMENT SUMMARY ==="
echo "$TAG containers:"
summary_container "$PG_CONTAINER"
summary_container "$RMQ_CONTAINER"
summary_container "$CHROMA_CONTAINER"
summary_container "$LB_CONTAINER"
summary_container "$TEMPLATE_CONTAINER"

echo ""
echo "$TAG services:"
# capelle-platform on the template yuta
if container_exists "$TEMPLATE_CONTAINER"; then
    if service_active_in_container "$TEMPLATE_CONTAINER" "capelle-platform.service"; then
        echo "  capelle-platform on ${TEMPLATE_CONTAINER}: active"
    else
        echo "  capelle-platform on ${TEMPLATE_CONTAINER}: NOT active"
    fi
fi
if container_exists "$LB_CONTAINER"; then
    if service_active_in_container "$LB_CONTAINER" "nginx"; then
        echo "  nginx on ${LB_CONTAINER}: active"
    fi
fi
if container_exists "$PG_CONTAINER"; then
    if service_active_in_container "$PG_CONTAINER" "postgresql"; then
        echo "  postgresql on ${PG_CONTAINER}: active"
    fi
fi
if container_exists "$RMQ_CONTAINER"; then
    if service_active_in_container "$RMQ_CONTAINER" "rabbitmq-server"; then
        echo "  rabbitmq-server on ${RMQ_CONTAINER}: active"
    fi
fi

echo ""
echo "$TAG next steps:"
if [[ "$SKIP_CUTOVER" = "1" || "$CUTOVER_YES" != "1" ]]; then
    echo "  - flip Cloudflare Tunnel: bash ${SCRIPT_DIR}/cutover-tunnel.sh --yes"
fi
cat <<EOF
  - smoke-test: ssh ${HOST} "lxc exec ${LB_CONTAINER} -- curl -s http://localhost:8080/api/health"
  - spawn second yuta: bash ${SCRIPT_DIR}/spawn-yuta.sh maki-zenin
  - watch logs: ssh ${HOST} "lxc exec ${TEMPLATE_CONTAINER} -- journalctl -u capelle-platform -f"
  - full runbook: deploy/PLAYBOOK.md
EOF

log "$STEP" "done"

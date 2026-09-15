#!/usr/bin/env bash
# =============================================================================
# scripts/migrate-chroma.sh
# -----------------------------------------------------------------------------
# Purpose:
#   One-shot migration of the live ChromaDB index from yuta-okkotsu (where
#   it was centralised after v1.0) to the standalone yuji-itadori container
#   (v1.1). After this runs, yuta-okkotsu's local chromadb.service is
#   stopped + disabled; new yutas will never run Chroma locally at all.
#
# Runs on:
#   The workstation. It SSHes into `gojo` and drives `lxc exec` from there.
#
# Flags:
#   --dry-run    Print every destructive command instead of executing it.
#
# Source data path (on yuta-okkotsu):
#   /var/lib/chroma
#
#   Why we believe that is the path:
#     The yuta provisioning ran `chroma run --path /var/lib/chroma ...`
#     inside the same systemd unit shape we're recreating on yuji-itadori.
#     Confirm before running real migration with:
#         ssh gojo "lxc exec yuta-okkotsu -- systemctl cat chromadb.service"
#     If that reveals a different --path, override here:
#         SRC_PATH=/actual/path ./scripts/migrate-chroma.sh
#
# Transfer strategy (and why):
#   LXD has no "lxc file copy container-to-container" primitive; every
#   push/pull goes via the host. Rather than spool the whole index onto
#   gojo's disk and back (doubling transient footprint), we stream it:
#
#       ssh gojo "lxc exec SRC -- tar -C $SRC_PATH -c ." \
#         | ssh gojo "lxc exec DST -- tar -C $DST_PATH -x"
#
#   This pipes a tar stream from the source container's stdout, through
#   two sshd sessions on gojo, into the destination container's stdin.
#   No intermediate file, no extra disk. The tradeoff is that we consume
#   twice the gojo-local tar throughput (one read + one write); for a
#   ~500 MB Chroma index this is comfortably under a minute.
#
# Safety rails:
#   - Stops chromadb.service on BOTH containers before transfer (fresh
#     target is fine but belt-and-braces — we don't want sqlite files
#     being written while we tar them).
#   - Does NOT delete the source index. Rollback = re-enable chromadb
#     on yuta-okkotsu and flip CAPELLE_CHROMA_HTTP back.
#   - Only disables yuta-okkotsu's service AFTER yuji-itadori is up and
#     responding to /api/v2/heartbeat with 200.
# =============================================================================

set -euo pipefail

HOST="gojo"
SRC_CT="yuta-okkotsu"
DST_CT="yuji-itadori"
SRC_PATH="${SRC_PATH:-/var/lib/chroma}"
DST_PATH="${DST_PATH:-/var/lib/chroma}"
TAG="[migrate-chroma]"

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,60p' "$0"
            exit 0
            ;;
        *)
            echo "$TAG unknown flag: $arg" >&2
            exit 2
            ;;
    esac
done

# run_remote wraps every state-changing ssh call so --dry-run prints
# instead of executes. Read-only probes (lxc info, heartbeat curl) run
# unconditionally because they don't mutate anything.
run_remote() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "$TAG (dry-run) ssh $HOST \"$*\""
    else
        # shellcheck disable=SC2029  # we deliberately want client-side expansion
        ssh "$HOST" "$*"
    fi
}

echo "$TAG === Migrating Chroma: ${SRC_CT}:${SRC_PATH} → ${DST_CT}:${DST_PATH} ==="
if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "$TAG DRY RUN — no destructive commands will execute."
fi

# ---- 0. sanity: both containers must exist --------------------------------
for ct in "$SRC_CT" "$DST_CT"; do
    if ! ssh "$HOST" "lxc info ${ct} >/dev/null 2>&1"; then
        echo "$TAG FATAL: container ${ct} does not exist on ${HOST}." >&2
        echo "$TAG        (run scripts/provision-yuji-itadori.sh first.)" >&2
        exit 1
    fi
done

# ---- 1. stop chromadb on both ---------------------------------------------
# Fresh target has nothing in flight, but stopping it unconditionally is
# the belt-and-braces the runbook calls for. Source stop is the important
# one — we can't tar a live sqlite WAL without risking corruption.
echo "$TAG [1/5] stopping chromadb.service on ${SRC_CT} and ${DST_CT}..."
run_remote "lxc exec ${SRC_CT} -- systemctl stop chromadb.service || true"
run_remote "lxc exec ${DST_CT} -- systemctl stop chromadb.service || true"

# ---- 2. ensure destination directory exists -------------------------------
echo "$TAG [2/5] preparing ${DST_PATH} on ${DST_CT}..."
run_remote "lxc exec ${DST_CT} -- mkdir -p ${DST_PATH}"

# ---- 3. stream tar from source to destination -----------------------------
# We do NOT use `lxc file push --recursive` because that requires a host-side
# staging step (pull → push) that doubles disk and halves throughput.
# Piping two `lxc exec`s through gojo's shell keeps us single-copy.
echo "$TAG [3/5] streaming ${SRC_PATH} → ${DST_CT}:${DST_PATH}..."
if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "$TAG (dry-run) ssh $HOST 'lxc exec ${SRC_CT} -- tar -C ${SRC_PATH} -cf - . | lxc exec ${DST_CT} -- tar -C ${DST_PATH} -xf -'"
else
    # Single ssh invocation so both lxc-exec ends share the same sshd pipe.
    # set -o pipefail inside the remote shell makes the source tar failing
    # (e.g. missing dir) abort the whole pipeline rather than silently
    # creating an empty DST.
    ssh "$HOST" 'bash -c "set -euo pipefail; lxc exec '"${SRC_CT}"' -- tar -C '"${SRC_PATH}"' -cf - . | lxc exec '"${DST_CT}"' -- tar -C '"${DST_PATH}"' -xf -"'
fi

# ---- 4. start chromadb on yuji-itadori + verify ---------------------------
echo "$TAG [4/5] starting chromadb.service on ${DST_CT}..."
run_remote "lxc exec ${DST_CT} -- systemctl start chromadb.service"

# Heartbeat wait — up to ~20s. Only probe in live mode (there's nothing
# to probe during dry-run).
HEARTBEAT_OK=0
if [[ "$DRY_RUN" -eq 0 ]]; then
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if ssh "$HOST" "lxc exec ${DST_CT} -- curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/v2/heartbeat" 2>/dev/null | grep -qx 200; then
            HEARTBEAT_OK=1
            break
        fi
        sleep 2
    done
    if [[ "$HEARTBEAT_OK" -ne 1 ]]; then
        echo "$TAG FATAL: ${DST_CT} heartbeat not 200 after migration." >&2
        echo "$TAG        Source index is still intact on ${SRC_CT}; do NOT disable it." >&2
        echo "$TAG        Inspect: ssh ${HOST} 'lxc exec ${DST_CT} -- journalctl -u chromadb -n 80'" >&2
        exit 1
    fi
fi

# ---- 5. decommission chromadb on yuta-okkotsu -----------------------------
# Source service stays stopped AND disabled so a yuta reboot doesn't
# relaunch it locally. We do NOT delete the data directory — that's the
# rollback path if something surfaces later.
echo "$TAG [5/5] disabling chromadb.service on ${SRC_CT} (source data preserved)..."
run_remote "lxc exec ${SRC_CT} -- systemctl disable chromadb.service || true"

echo ""
echo "$TAG === migration complete ==="
if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "$TAG dry-run only — re-run without --dry-run to execute."
else
    echo "$TAG live Chroma:  http://${DST_CT}:8000  (heartbeat 200)"
    echo "$TAG source state: stopped + disabled on ${SRC_CT} (data intact at ${SRC_PATH})"
    echo "$TAG next step:    set CAPELLE_CHROMA_HTTP=${DST_CT}:8000 in /etc/capelle-codex.env"
    echo "$TAG               on every yuta and restart capelle-platform.service."
fi

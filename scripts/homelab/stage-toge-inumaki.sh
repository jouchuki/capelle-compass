#!/usr/bin/env bash
# =============================================================================
# scripts/stage-toge-inumaki.sh
# -----------------------------------------------------------------------------
# Purpose:
#   One-shot: stand up a STAGING ChromaDB container named `toge-inumaki` on
#   host `maki` (bare-metal, LAN hostname `maki-zenin`), seeded with a live
#   copy of prod data currently in `gojo:yuji-itadori`. Gojo's yuji-itadori
#   keeps running throughout — this script NEVER stops, disables, or touches
#   the prod chromadb service.
#
#   Why toge-inumaki (not yuji-itadori): staging name must differ from prod
#   so yutas keep pointing at prod via CAPELLE_CHROMA_HTTP=yuji-itadori:8000;
#   anything that wants to exercise the staging copy can opt in with
#   CAPELLE_CHROMA_HTTP=toge-inumaki:8000.
#
#   (JJK-ish naming: Inumaki talks in rice-ball ingredients — a side-kick
#   voice to Itadori. Feels right for a read-mostly staging replica.)
#
# Runs on:
#   The workstation. SSHes into gojo (for the source tar) and maki (for LXD
#   provisioning + tar extract). maki itself is reached via your existing
#   ~/.ssh/config (ProxyJump through griffith); this script does not care.
#
# Required env:
#   TAILSCALE_AUTHKEY  Reusable auth key (container joins tailnet as `toge-inumaki`)
#   GHOST_PUBKEY_FILE  Path to the ghost-laptop public key — the ONLY key
#                      allowed to SSH into the new container. Everyone else
#                      hits the box through port 8000 / chroma HTTP only.
#   MAKI_SUDO_PW       Sudo password for jouchuki@maki. The script installs a
#                      short-lived askpass helper on maki (~/.toge-askpass.sh,
#                      mode 700) so `sudo -A` works non-interactively — no
#                      sudoers edit, no NOPASSWD. Cleaned up on EXIT.
#
# Flags:
#   --dry-run          Print every state-changing command instead of executing.
#   --skip-seed        Provision only, do not copy prod data. Leaves an empty
#                      chroma. Useful if you want to seed separately.
#
# What happens to prod (gojo:yuji-itadori):
#   Nothing. No stop, no disable, no config change. We live-tar its
#   /var/lib/chroma while chromadb is running. Caveat: sqlite files (chroma's
#   metadata store) are captured mid-write, so the staging seed may be
#   microscopically inconsistent. Chroma is write-light and sqlite replays
#   its WAL on open, so in practice the staging copy loads fine; treat it as
#   "close to prod at seed time", not a transactional snapshot.
#
# Rough timeline on the stock 2GB/2CPU container + 680M index:
#   provision    ~2-3 min (apt + pip install chromadb)
#   tar stream   <1 min  (workstation is the bottleneck — gojo → ws → maki)
#   first start  ~10-20s (chroma indexes warm-up)
#
# Idempotency:
#   If `toge-inumaki` already exists on maki, provisioning steps are skipped;
#   re-running with seeding enabled will just re-pull fresh prod data into
#   the existing container (after stopping chromadb on maki only).
# =============================================================================

set -euo pipefail

SRC_HOST="gojo"
DST_HOST="maki"
SRC_CT="yuji-itadori"
DST_CT="toge-inumaki"
SRC_PATH="${SRC_PATH:-/var/lib/chroma}"
DST_PATH="${DST_PATH:-/var/lib/chroma}"
TAG="[stage-toge-inumaki]"

# ---- flag parsing ----------------------------------------------------------
DRY_RUN=0
SKIP_SEED=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)   DRY_RUN=1 ;;
        --skip-seed) SKIP_SEED=1 ;;
        -h|--help)   sed -n '2,60p' "$0"; exit 0 ;;
        *) echo "$TAG unknown flag: $arg" >&2; exit 2 ;;
    esac
done

# ---- env checks ------------------------------------------------------------
: "${TAILSCALE_AUTHKEY:?$TAG TAILSCALE_AUTHKEY is required}"
: "${GHOST_PUBKEY_FILE:?$TAG GHOST_PUBKEY_FILE is required (path to ghost-laptop pubkey)}"
: "${MAKI_SUDO_PW:?$TAG MAKI_SUDO_PW is required (sudo password for jouchuki@maki)}"
[[ -r "$GHOST_PUBKEY_FILE" ]] || { echo "$TAG cannot read GHOST_PUBKEY_FILE=$GHOST_PUBKEY_FILE" >&2; exit 2; }

echo "$TAG === staging ${DST_CT} on ${DST_HOST} (seed from ${SRC_HOST}:${SRC_CT}) ==="
[[ "$DRY_RUN" -eq 1 ]] && echo "$TAG DRY RUN — no state changes"

# ---- askpass install on maki ----------------------------------------------
# Pattern: pipe the password into a file on maki (0600), then drop a tiny
# shell script that `cat`s it when sudo asks. `SUDO_ASKPASS=... sudo -A`
# reads stdout of that script — which means our real stdin is free to carry
# piped data (e.g. the tar stream in the seed step). On EXIT we shred both.
install_askpass() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "$TAG (dry-run) install askpass on ${DST_HOST}:~/.toge-askpass.sh"
        return
    fi
    # shellcheck disable=SC2016  # the $HOME needs to expand on maki, not here
    printf '%s' "$MAKI_SUDO_PW" | ssh "$DST_HOST" '
        umask 077
        cat > "$HOME/.toge-askpass-raw"
        printf "%s\n%s\n" "#!/bin/sh" "cat \"\$HOME/.toge-askpass-raw\"" > "$HOME/.toge-askpass.sh"
        chmod 700 "$HOME/.toge-askpass.sh"
        chmod 600 "$HOME/.toge-askpass-raw"
    '
}
cleanup_askpass() {
    ssh "$DST_HOST" 'rm -f "$HOME/.toge-askpass.sh" "$HOME/.toge-askpass-raw"' 2>/dev/null || true
}
trap cleanup_askpass EXIT

install_askpass

# Wrappers. maki_sh runs a remote bash script via sudo -A on maki. maki_sh_pipe
# is the same but preserves stdin for piping (tar stream); it takes the remote
# command as a single string, no heredoc.
maki_sh() {
    # usage: maki_sh <<'REMOTE' ... REMOTE
    if [[ "$DRY_RUN" -eq 1 ]]; then
        cat >/dev/null  # consume heredoc
        echo "$TAG (dry-run) ssh $DST_HOST 'SUDO_ASKPASS=... sudo -A bash -s' <<<HEREDOC>"
        return
    fi
    # shellcheck disable=SC2016
    ssh "$DST_HOST" 'SUDO_ASKPASS="$HOME/.toge-askpass.sh" sudo -A bash -s'
}
maki_cmd() {
    # usage: maki_cmd 'remote shell snippet'
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "$TAG (dry-run) ssh $DST_HOST 'sudo -A $1'"
        return
    fi
    # shellcheck disable=SC2016
    ssh "$DST_HOST" "SUDO_ASKPASS=\"\$HOME/.toge-askpass.sh\" sudo -A $1"
}

# ---- preflight: sudo works, source container up ---------------------------
echo "$TAG preflight — verify sudo on ${DST_HOST} and source on ${SRC_HOST}..."
if [[ "$DRY_RUN" -eq 0 ]]; then
    ssh "$DST_HOST" 'SUDO_ASKPASS="$HOME/.toge-askpass.sh" sudo -A true' \
        || { echo "$TAG FATAL: sudo -A failed on ${DST_HOST} — wrong MAKI_SUDO_PW?" >&2; exit 1; }
    ssh "$SRC_HOST" "lxc info ${SRC_CT} >/dev/null 2>&1" \
        || { echo "$TAG FATAL: ${SRC_CT} not found on ${SRC_HOST}" >&2; exit 1; }
fi

# ---- phase A: provision toge-inumaki on maki (idempotent) -----------------
DST_EXISTS=0
if [[ "$DRY_RUN" -eq 0 ]]; then
    if ssh "$DST_HOST" 'SUDO_ASKPASS="$HOME/.toge-askpass.sh" sudo -A lxc info '"${DST_CT}"' >/dev/null 2>&1'; then
        DST_EXISTS=1
    fi
fi

if [[ "$DST_EXISTS" -eq 1 ]]; then
    echo "$TAG [A] ${DST_CT} already exists on ${DST_HOST} — skipping provisioning."
else
    echo "$TAG [A1] launching LXC container ubuntu:24.04 ${DST_CT}..."
    maki_cmd "lxc launch ubuntu:24.04 ${DST_CT} -c limits.memory=2GB -c limits.cpu=2"
    [[ "$DRY_RUN" -eq 0 ]] && sleep 5

    echo "$TAG [A2] installing python3 + chromadb inside ${DST_CT}..."
    maki_sh <<REMOTE
set -euo pipefail
lxc exec ${DST_CT} -- bash -c '
    set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip curl ca-certificates >/dev/null
    python3 -m venv /opt/chroma
    /opt/chroma/bin/pip install --quiet --upgrade pip
    /opt/chroma/bin/pip install --quiet "chromadb>=0.5.0"
    mkdir -p /var/lib/chroma
'
REMOTE

    echo "$TAG [A3] writing chromadb.service (not started yet — no data)..."
    maki_sh <<REMOTE
set -euo pipefail
lxc exec ${DST_CT} -- bash -c '
cat >/etc/systemd/system/chromadb.service <<UNIT
[Unit]
Description=ChromaDB vector store (staging, toge-inumaki)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/var/lib/chroma
ExecStart=/opt/chroma/bin/chroma run --path /var/lib/chroma --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
'
REMOTE

    echo "$TAG [A4] installing ghost-laptop key + locking sshd (container only)..."
    # Pipe ghost pubkey contents into the container's authorized_keys. Only that
    # key can SSH into the container; no passwords, no other keys. Maki host
    # sshd is unaffected.
    if [[ "$DRY_RUN" -eq 0 ]]; then
        # shellcheck disable=SC2016
        cat "$GHOST_PUBKEY_FILE" | ssh "$DST_HOST" '
            SUDO_ASKPASS="$HOME/.toge-askpass.sh" sudo -A lxc exec '"${DST_CT}"' -- \
                bash -c "mkdir -p /root/.ssh && chmod 700 /root/.ssh && cat > /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys"
        '
    else
        echo "$TAG (dry-run) pipe $GHOST_PUBKEY_FILE -> ${DST_CT}:/root/.ssh/authorized_keys"
    fi
    maki_sh <<REMOTE
set -euo pipefail
lxc exec ${DST_CT} -- bash -c '
    sed -i -E "s/^#?PasswordAuthentication.*/PasswordAuthentication no/" /etc/ssh/sshd_config
    sed -i -E "s/^#?PermitRootLogin.*/PermitRootLogin prohibit-password/" /etc/ssh/sshd_config
    sed -i -E "s/^#?PubkeyAuthentication.*/PubkeyAuthentication yes/" /etc/ssh/sshd_config
    systemctl reload ssh 2>/dev/null || systemctl restart ssh
'
REMOTE

    echo "$TAG [A5] joining tailnet as ${DST_CT}..."
    maki_sh <<REMOTE
set -euo pipefail
lxc exec ${DST_CT} -- bash -c "
    set -euo pipefail
    if ! command -v tailscale >/dev/null 2>&1; then
        curl -fsSL https://tailscale.com/install.sh | sh
    fi
    tailscale up --authkey='${TAILSCALE_AUTHKEY}' --hostname='${DST_CT}' --reset
"
REMOTE
fi

# ---- phase B: seed data from prod (live tar, gojo → ws → maki) ------------
if [[ "$SKIP_SEED" -eq 1 ]]; then
    echo "$TAG [B] --skip-seed: leaving ${DST_CT} empty."
else
    echo "$TAG [B1] stopping chromadb on ${DST_CT} (prod ${SRC_CT} untouched)..."
    maki_cmd "lxc exec ${DST_CT} -- systemctl stop chromadb.service || true"

    echo "$TAG [B2] clearing ${DST_PATH} on ${DST_CT} (re-seed safe)..."
    maki_cmd "lxc exec ${DST_CT} -- bash -c 'rm -rf ${DST_PATH} && mkdir -p ${DST_PATH}'"

    echo "$TAG [B3] live-tar ${SRC_HOST}:${SRC_CT}:${SRC_PATH} → ${DST_HOST}:${DST_CT}:${DST_PATH}..."
    echo "$TAG       (prod chromadb keeps serving; sqlite-in-flight caveat — see header)"
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "$TAG (dry-run) ssh $SRC_HOST 'lxc exec $SRC_CT -- tar -C $SRC_PATH -cf - .' | ssh $DST_HOST 'sudo -A lxc exec $DST_CT -- tar -C $DST_PATH -xf -'"
    else
        set -o pipefail
        # shellcheck disable=SC2016
        ssh "$SRC_HOST" "lxc exec ${SRC_CT} -- tar -C ${SRC_PATH} -cf - ." \
            | ssh "$DST_HOST" 'SUDO_ASKPASS="$HOME/.toge-askpass.sh" sudo -A lxc exec '"${DST_CT}"' -- tar -C '"${DST_PATH}"' -xf -'
    fi
fi

# ---- start chromadb on toge-inumaki + verify ------------------------------
echo "$TAG [C] enabling + starting chromadb on ${DST_CT}..."
maki_cmd "lxc exec ${DST_CT} -- systemctl enable --now chromadb.service"

HEARTBEAT_OK=0
if [[ "$DRY_RUN" -eq 0 ]]; then
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if ssh "$DST_HOST" 'SUDO_ASKPASS="$HOME/.toge-askpass.sh" sudo -A lxc exec '"${DST_CT}"' -- curl -fsS -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/api/v2/heartbeat' 2>/dev/null | grep -qx 200; then
            HEARTBEAT_OK=1
            break
        fi
        sleep 2
    done
fi

TS_IP=""
if [[ "$DRY_RUN" -eq 0 ]]; then
    TS_IP="$(ssh "$DST_HOST" 'SUDO_ASKPASS="$HOME/.toge-askpass.sh" sudo -A lxc exec '"${DST_CT}"' -- tailscale ip -4' 2>/dev/null | head -n1 || true)"
fi

echo ""
echo "$TAG === ${DST_CT} staged ==="
echo "$TAG tailnet name:  ${DST_CT}"
[[ -n "$TS_IP" ]] && echo "$TAG tailnet IP:    ${TS_IP}"
echo "$TAG HTTP URL:      http://${DST_CT}:8000"
echo "$TAG SSH:           only the ghost-laptop key is authorized"
if [[ "$DRY_RUN" -eq 0 ]]; then
    if [[ "$HEARTBEAT_OK" -eq 1 ]]; then
        echo "$TAG heartbeat:     200 OK"
    else
        echo "$TAG heartbeat:     NOT YET — check 'ssh ${DST_HOST} sudo -A lxc exec ${DST_CT} -- journalctl -u chromadb -n 80'" >&2
    fi
fi
echo "$TAG prod untouched:  ${SRC_HOST}:${SRC_CT} still serving on yuji-itadori:8000"
echo "$TAG opt into staging: CAPELLE_CHROMA_HTTP=${DST_CT}:8000"

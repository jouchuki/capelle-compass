#!/usr/bin/env bash
# =============================================================================
# scripts/harden-yuta.sh
# -----------------------------------------------------------------------------
# Purpose:
#   Idempotently apply the Day 1 security lockdown to a yuta-* container:
#     1. Create the unprivileged `capelle-agent` system user (uid under 1000)
#        and the `capelle-privileged` group (mode gate for probe binaries).
#     2. chmod 750 (owner=root, group=capelle-privileged) on the network/
#        process/hardware-introspection binaries so the ohrs subprocess
#        (which runs as capelle-agent) cannot exec them. Root + members of
#        capelle-privileged retain exec; capelle-agent is NOT in that group.
#
#   Pairs with the systemd-run wrapper in impl_ohrs.py (`_wrap_in_systemd_run`):
#   the wrapper only kicks in when capelle-agent exists; otherwise ohrs spawns
#   directly as before. So this script is the "flip the switch" step.
#
# Usage:
#   ./harden-yuta.sh <container>                       # applies over SSH to gojo
#   HOST=maki ./harden-yuta.sh <container>             # different LXD host
#
#   Rerunning is safe — every step is probe-then-act.
#
# Rollback:
#   userdel capelle-agent       # disables the sandbox; ohrs spawns as root again
#   for b in /usr/bin/hostname /usr/bin/ss ...; do chgrp root "$b" && chmod 755 "$b"; done
# =============================================================================

set -euo pipefail

CT="${1:?usage: $0 <container>}"
HOST="${HOST:-gojo}"
TAG="[harden:${CT}]"

echo "$TAG === applying lockdown ==="

ssh "$HOST" "lxc exec ${CT} -- bash -s" <<'REMOTE'
set -euo pipefail

# -- 1. capelle-privileged group + capelle-agent user -----------------------
if ! getent group capelle-privileged >/dev/null; then
    groupadd --system capelle-privileged
    echo "  created group capelle-privileged"
else
    echo "  group capelle-privileged already exists"
fi

if ! getent passwd capelle-agent >/dev/null; then
    useradd \
        --system \
        --no-create-home \
        --home-dir /opt/capelle-shared \
        --shell /usr/sbin/nologin \
        --comment "ohrs analyst agent (unprivileged)" \
        capelle-agent
    echo "  created user capelle-agent (uid $(id -u capelle-agent))"
else
    # Fix older installs that pointed HOME at /tmp/capelle-jobs.
    current_home=$(getent passwd capelle-agent | cut -d: -f6)
    if [ "$current_home" != "/opt/capelle-shared" ]; then
        usermod -d /opt/capelle-shared capelle-agent
        echo "  moved capelle-agent home: $current_home -> /opt/capelle-shared"
    else
        echo "  user capelle-agent already exists (uid $(id -u capelle-agent), home /opt/capelle-shared)"
    fi
fi

# Guardrail: capelle-agent MUST NOT be in capelle-privileged.
if id -nG capelle-agent | tr ' ' '\n' | grep -qx capelle-privileged; then
    echo "  FATAL: capelle-agent is in capelle-privileged — refusing to continue"
    exit 1
fi

# -- 1b. /opt/capelle-shared layout -----------------------------------------
# The agent's HOME. Read-only from the agent's perspective (root:root, 0755
# dirs, 0644 files) — tools that look under ~/.local/share/... find the
# catalog naturally, but the agent cannot mutate it. Any writes happen in
# \$OPENHARNESSRS_DATA_DIR instead.
mkdir -p /opt/capelle-shared/.local/share/cbs-tool
# One-time relocation: legacy catalogs lived under /root/.local/share/cbs-tool
# (root-only directory, agent couldn't traverse it). Move them over so they
# sit under the agent's home in the canonical XDG path.
for f in catalog.json municipalities.json enriched_catalog.jsonl; do
    src=/root/.local/share/cbs-tool/$f
    dst=/opt/capelle-shared/.local/share/cbs-tool/$f
    if [ -f "$src" ] && [ ! -f "$dst" ]; then
        cp "$src" "$dst"
        echo "  copied /root/.local/share/cbs-tool/$f -> /opt/capelle-shared/.local/share/cbs-tool/"
    fi
done
chmod -R a+rX /opt/capelle-shared
if [ -f /opt/capelle-shared/.local/share/cbs-tool/catalog.json ]; then
    if sudo -u capelle-agent head -c 1 /opt/capelle-shared/.local/share/cbs-tool/catalog.json >/dev/null 2>&1; then
        echo "  acceptance: capelle-agent can read the CBS catalog"
    else
        echo "  FATAL: capelle-agent cannot read /opt/capelle-shared/.local/share/cbs-tool/catalog.json"
        exit 1
    fi
else
    echo "  WARN: /opt/capelle-shared/.local/share/cbs-tool/catalog.json not populated yet — copy it over before first job"
fi

# -- 2. Probe / discovery binaries: chgrp + chmod 750 -----------------------
PROBE_BINS=(
    /usr/bin/hostname /usr/bin/uname /usr/bin/lscpu
    /usr/bin/ss /usr/sbin/netstat /usr/sbin/ip /usr/sbin/ifconfig /usr/sbin/route /usr/sbin/arp
    /usr/bin/ps /usr/bin/top /usr/bin/pidstat
    /usr/bin/ping /usr/sbin/ping /usr/bin/traceroute /usr/bin/tracepath
    /usr/bin/curl /usr/bin/wget /usr/bin/nc.openbsd /usr/bin/ncat /usr/bin/socat
    /usr/bin/dig /usr/bin/nslookup /usr/bin/host
    /usr/bin/mount /usr/bin/findmnt /usr/bin/df /usr/bin/free
    /usr/bin/dmesg /usr/bin/journalctl /usr/bin/systemctl
    /usr/bin/tailscale /usr/sbin/tailscale /usr/local/bin/tailscale
)
changed=0
skipped=0
for b in "${PROBE_BINS[@]}"; do
    if [ ! -e "$b" ]; then
        continue
    fi
    cur_mode=$(stat -c '%a' "$b")
    cur_grp=$(stat -c '%G' "$b")
    if [ "$cur_mode" = "750" ] && [ "$cur_grp" = "capelle-privileged" ]; then
        skipped=$((skipped+1))
        continue
    fi
    chgrp capelle-privileged "$b"
    chmod 750 "$b"
    changed=$((changed+1))
done
echo "  probe binaries: ${changed} updated, ${skipped} already locked"

# -- 3. Acceptance check ----------------------------------------------------
# capelle-agent must NOT be able to exec the probe binaries.
if sudo -u capelle-agent /usr/bin/hostname >/dev/null 2>&1; then
    echo "  FATAL: capelle-agent can still exec /usr/bin/hostname"
    exit 1
fi
echo "  acceptance: capelle-agent cannot exec /usr/bin/hostname (expected)"

# Root must still be able to.
if ! /usr/bin/hostname >/dev/null 2>&1; then
    echo "  FATAL: root cannot exec /usr/bin/hostname — chmod went wrong"
    exit 1
fi
echo "  acceptance: root can still exec /usr/bin/hostname (expected)"

echo "  === lockdown applied ==="
REMOTE

echo "$TAG done"

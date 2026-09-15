#!/usr/bin/env bash
# Deploy Capelle Data Assistent to an LXC container on gojo
# Usage: bash scripts/deploy-lxc.sh [container-name] [tskey]
#
# Prerequisites:
#   - SSH access to gojo via ProxyJump through griffith
#   - Tailscale auth key (reusable, from admin console)
#   - LXD running on gojo with ubuntu:24.04 image available
#   - capelle-repo-agent at REPO_AGENT_DIR (for ohrs skills, CLI tools, ChromaDB)

set -euo pipefail

CONTAINER="${1:-yuta-okkotsu}"
TSKEY="${2:-$(grep TSKEY /home/jouchuki2/safetrace/local-lab-cluster/.env | cut -d= -f2)}"
HOST="gojo"
DEPLOY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_AGENT_DIR="/home/jouchuki2/hobby/capelle-repo-agent"
# ohrs binary: by default fetched from the GitHub release via
# scripts/fetch-ohrs.sh (no local Rust build needed). Set OHRS_BINARY to a
# local path to push your own build instead; OHRS_VERSION pins the release tag.
OHRS_VERSION="${OHRS_VERSION:-v1.0.0}"
OHRS_BINARY="${OHRS_BINARY:-}"
SSH_PUBKEY="$(cat ~/.ssh/id_ed25519.pub 2>/dev/null || cat ~/.ssh/id_rsa.pub)"

echo "=== Deploying Capelle Platform to ${CONTAINER} on ${HOST} ==="

# 1. Create container
echo "[1/9] Creating LXC container..."
ssh "$HOST" "lxc delete ${CONTAINER} --force 2>/dev/null || true"
ssh "$HOST" "lxc launch ubuntu:24.04 ${CONTAINER} -c limits.memory=1GB -c limits.cpu=2"
sleep 5

# 2. Install Tailscale
echo "[2/9] Installing Tailscale..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -c '
curl -fsSL https://tailscale.com/install.sh | sh 2>/dev/null
tailscale up --authkey=${TSKEY} --hostname=${CONTAINER}
'"
TS_IP=$(ssh "$HOST" "lxc exec ${CONTAINER} -- tailscale ip -4")
echo "    Tailscale IP: ${TS_IP}"

# 3. Add SSH key
echo "[3/9] Adding SSH key..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -c '
mkdir -p /root/.ssh
echo \"${SSH_PUBKEY}\" >> /root/.ssh/authorized_keys
chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys
'"

# 4. Install system deps
echo "[4/9] Installing Python + Node..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -c '
apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip nodejs npm > /dev/null 2>&1
'"

# 5. Push platform app code
echo "[5/9] Pushing platform app code..."
cd "$DEPLOY_DIR"
tar czf /tmp/capelle-deploy.tar.gz \
    --exclude=node_modules --exclude=.venv --exclude=__pycache__ \
    --exclude=.git --exclude='*.db' --exclude=scripts \
    backend/ frontend/
scp -q /tmp/capelle-deploy.tar.gz "${HOST}:/tmp/"
ssh "$HOST" "lxc file push /tmp/capelle-deploy.tar.gz ${CONTAINER}/opt/"
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -c 'cd /opt && tar xzf capelle-deploy.tar.gz && rm capelle-deploy.tar.gz'"

# 6. Push capelle-repo-agent source (code only, no big data)
echo "[6/9] Pushing capelle-repo-agent code (skills + CLI sources + .env)..."
cd "$REPO_AGENT_DIR"
tar czf /tmp/capelle-repo-light.tar.gz \
    --exclude=.git --exclude=node_modules --exclude=__pycache__ --exclude=.venv \
    --exclude=platform --exclude='*.pyc' \
    --exclude='capelle_rag/chroma_db' --exclude='*.sqlite3' \
    --exclude='Capelle_budget/output/.DS_Store' \
    .claude/ .openharnessrs/ skills/ \
    cbs_tool/ Capelle_beleid/ Capelle_budget/ Capelle_buitenbeter/ \
    capelle_rag/ .env
scp /tmp/capelle-repo-light.tar.gz "${HOST}:/tmp/"
ssh "$HOST" "lxc file push /tmp/capelle-repo-light.tar.gz ${CONTAINER}/opt/capelle-repo-agent.tar.gz"
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -c '
mkdir -p /opt/capelle-repo-agent
cd /opt/capelle-repo-agent && tar xzf /opt/capelle-repo-agent.tar.gz && rm /opt/capelle-repo-agent.tar.gz
'"

# 6b. Push ChromaDB + CBS catalog (big data)
echo "[6b/9] Pushing ChromaDB + CBS catalog..."
tar cf - -C "$REPO_AGENT_DIR" capelle_rag/chroma_db | zstd -T0 -3 -o /tmp/chroma_db.tar.zst
scp /tmp/chroma_db.tar.zst "${HOST}:/tmp/"
ssh "$HOST" "lxc file push /tmp/chroma_db.tar.zst ${CONTAINER}/opt/"
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -c '
apt-get install -y -qq zstd > /dev/null 2>&1
cd /opt/capelle-repo-agent && rm -rf capelle_rag/chroma_db
zstd -d /opt/chroma_db.tar.zst -c | tar xf -
rm /opt/chroma_db.tar.zst
'"

# CBS catalog (from ~/.local/share/cbs-tool)
if [ -d "$HOME/.local/share/cbs-tool" ]; then
    tar cf - -C "$HOME/.local/share/cbs-tool" catalog.json enriched_catalog.jsonl municipalities.json 2>/dev/null | zstd -T0 -3 -o /tmp/cbs-data.tar.zst
    scp /tmp/cbs-data.tar.zst "${HOST}:/tmp/"
    ssh "$HOST" "lxc file push /tmp/cbs-data.tar.zst ${CONTAINER}/tmp/"
    ssh "$HOST" "lxc exec ${CONTAINER} -- bash -c '
mkdir -p /root/.local/share/cbs-tool
cd /root/.local/share/cbs-tool && zstd -d /tmp/cbs-data.tar.zst -c | tar xf -
rm /tmp/cbs-data.tar.zst
'"
fi

# 7. Push ohrs binary
echo "[7/9] Pushing ohrs binary..."
if [[ -z "$OHRS_BINARY" ]]; then
  OHRS_BINARY="$(OHRS_VERSION="$OHRS_VERSION" bash "$DEPLOY_DIR/scripts/fetch-ohrs.sh")"
fi
scp "$OHRS_BINARY" "${HOST}:/tmp/ohrs"
ssh "$HOST" "lxc file push /tmp/ohrs ${CONTAINER}/usr/local/bin/ohrs"
ssh "$HOST" "lxc exec ${CONTAINER} -- chmod +x /usr/local/bin/ohrs"

# 8. Install all deps (platform + CLI tools)
echo "[8/9] Installing deps..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -c '
# Platform backend
cd /opt/backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -q 2>&1 | tail -1

# Frontend build
cd /opt/frontend && npm install -q 2>/dev/null && npm run build 2>&1 | tail -1

# CLI tools in a shared venv
python3 -m venv /opt/tools-venv
cd /opt/capelle-repo-agent/cbs_tool && /opt/tools-venv/bin/pip install -e . -q 2>&1 | tail -1
cd /opt/capelle-repo-agent/Capelle_beleid && /opt/tools-venv/bin/pip install -e . -q 2>&1 | tail -1
cd /opt/capelle-repo-agent/Capelle_budget && /opt/tools-venv/bin/pip install -e . -q 2>&1 | tail -1
cd /opt/capelle-repo-agent/Capelle_buitenbeter && /opt/tools-venv/bin/pip install -e . -q 2>&1 | tail -1

# Symlink CLI tools into PATH
ln -sf /opt/tools-venv/bin/cbs /usr/local/bin/cbs
ln -sf /opt/tools-venv/bin/capelle-beleid /usr/local/bin/capelle-beleid
ln -sf /opt/tools-venv/bin/capelle-budget /usr/local/bin/capelle-budget
ln -sf /opt/tools-venv/bin/capelle-buitenbeter /usr/local/bin/capelle-buitenbeter
'"

# 9. Create and start systemd service
echo "[9/9] Starting service..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -c '
cat > /etc/systemd/system/capelle-platform.service << SVCEOF
[Unit]
Description=Capelle Data Assistent Platform
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/backend
ExecStart=/opt/backend/.venv/bin/python -m capelle_platform.main
Restart=on-failure
RestartSec=5
Environment=CAPELLE_HOST=0.0.0.0
Environment=CAPELLE_PORT=8080
Environment=CAPELLE_LOG_LEVEL=INFO
Environment=CAPELLE_OHRS_WORKING_DIR=/opt/capelle-repo-agent
Environment=CAPELLE_OHRS_BINARY=/usr/local/bin/ohrs

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable capelle-platform
systemctl start capelle-platform
'"
sleep 3

# NOTE: Cloudflare Tunnel was historically installed here on each yuta via
# CF_TUNNEL_TOKEN. That responsibility has moved to the nginx LB container
# — see scripts/provision-masamichi-yaga.sh. Yutas no longer run their own
# cloudflared; they sit behind masamichi-yaga:80 which nginx proxies to.

# Verify
echo ""
echo "=== Deployment complete ==="
echo "Container:  ${CONTAINER}"
echo "Tailscale:  ${TS_IP}"
echo "MagicDNS:   http://${CONTAINER}:8080"
echo "Health:     $(curl -s http://${TS_IP}:8080/api/health)"
echo ""
echo "Tools installed:"
ssh "$HOST" "lxc exec ${CONTAINER} -- ohrs --version"
ssh "$HOST" "lxc exec ${CONTAINER} -- cbs --help 2>&1 | head -1"

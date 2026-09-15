#!/usr/bin/env bash
# =============================================================================
# scripts/provision-aoi-todo.sh
# -----------------------------------------------------------------------------
# Purpose:
#   Provision the shared Postgres container `aoi-todo` on host `gojo`. This
#   becomes the single source of truth for all yuta-* instances once the
#   backend is swung over to the Postgres store.
#
# Runs on:
#   The workstation. It SSHes into `gojo` and drives `lxc` from there. It
#   does NOT run inside any container.
#
# Required env:
#   CAPELLE_PG_PASSWORD   Password for the `capelle` Postgres role (set here,
#                         then handed to yutas via CAPELLE_POSTGRES_DSN).
#   TAILSCALE_AUTHKEY     Reusable auth key so the container joins the tailnet
#                         with hostname `aoi-todo`.
#
# Idempotency:
#   If the container already exists, the script exits cleanly (does NOT
#   destroy or reconfigure — that belongs to a dedicated re-provisioner).
#   Otherwise it creates the container, installs Postgres 16, creates the
#   role + database, pins pg_hba for the tailnet CIDR, and joins Tailscale.
#   Re-running after a partial failure is safe: the DB role + database
#   creation steps use IF NOT EXISTS / DO $$ ... $$ guards.
# =============================================================================

set -euo pipefail

CONTAINER="aoi-todo"
HOST="gojo"
TAG="[provision-aoi-todo]"

# ---- env checks ------------------------------------------------------------
: "${CAPELLE_PG_PASSWORD:?$TAG CAPELLE_PG_PASSWORD is required (password for the capelle Postgres role)}"
: "${TAILSCALE_AUTHKEY:?$TAG TAILSCALE_AUTHKEY is required (reusable key from the Tailscale admin console)}"

echo "$TAG === Provisioning ${CONTAINER} on ${HOST} ==="

# ---- early-out if already present ------------------------------------------
if ssh "$HOST" "lxc info ${CONTAINER} >/dev/null 2>&1"; then
    echo "$TAG container ${CONTAINER} already exists on ${HOST} — exiting (no-op)."
    exit 0
fi

# ---- 1. launch container ---------------------------------------------------
echo "$TAG [1/6] launching LXC container (ubuntu:24.04)..."
ssh "$HOST" "lxc launch ubuntu:24.04 ${CONTAINER} -c limits.memory=2GB -c limits.cpu=2"
sleep 5

# ---- 2. install postgresql-16 ---------------------------------------------
# 24.04 (noble) ships postgresql-16 in universe, but we pin via the PGDG repo
# so future 24.04 point-releases don't drift us to a different major.
echo "$TAG [2/6] installing postgresql-16 (via PGDG repo)..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq curl ca-certificates gnupg lsb-release >/dev/null

install -d /usr/share/postgresql-common/pgdg
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc

CODENAME="$(lsb_release -cs)"
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${CODENAME}-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list

apt-get update -qq
apt-get install -y -qq postgresql-16 >/dev/null
REMOTE

# ---- 3. create role + database --------------------------------------------
# We inject the password via stdin (never in argv) to avoid it landing in
# any process list on the gojo host or inside the container.
echo "$TAG [3/6] creating role 'capelle' and database 'capelle'..."
ssh "$HOST" "lxc exec ${CONTAINER} -- env CAPELLE_PG_PASSWORD=${CAPELLE_PG_PASSWORD} bash -s" <<'REMOTE'
set -euo pipefail
: "${CAPELLE_PG_PASSWORD:?capelle password missing inside container}"

# psql client-side variable substitution. The ``\if`` / ``\else`` /
# ``\endif`` branch happens BEFORE statements are sent to the server,
# so ``:'capelle_pw'`` is resolved client-side into a single-quote-
# escaped literal — safe from SQL injection. We intentionally avoid a
# DO block here: psql's ``:var`` substitution is not visible inside
# a server-side DO block.
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
\set capelle_pw '${CAPELLE_PG_PASSWORD}'

SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='capelle') AS role_exists \gset
\if :role_exists
    ALTER ROLE capelle WITH LOGIN PASSWORD :'capelle_pw';
\else
    CREATE ROLE capelle LOGIN PASSWORD :'capelle_pw';
\endif
SQL

# psql can't CREATE DATABASE from inside a transaction block, so do it separately.
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='capelle'" | grep -q 1; then
    sudo -u postgres createdb --owner=capelle capelle
fi
REMOTE

# ---- 4. pin pg_hba + listen_addresses -------------------------------------
echo "$TAG [4/6] opening listen_addresses + pg_hba for tailnet CIDR..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<'REMOTE'
set -euo pipefail

PGCONF="$(ls /etc/postgresql/16/main/postgresql.conf)"
HBA="$(ls /etc/postgresql/16/main/pg_hba.conf)"

# listen_addresses = '*'
if grep -qE "^[#[:space:]]*listen_addresses" "$PGCONF"; then
    sed -i "s|^[#[:space:]]*listen_addresses.*|listen_addresses = '*'|" "$PGCONF"
else
    echo "listen_addresses = '*'" >> "$PGCONF"
fi

# Tailnet rule (100.64.0.0/10 is the CGNAT range Tailscale uses).
HBA_RULE="host    capelle    capelle    100.64.0.0/10    scram-sha-256"
if ! grep -qF "$HBA_RULE" "$HBA"; then
    echo "$HBA_RULE" >> "$HBA"
fi
REMOTE

# ---- 5. enable + start postgres -------------------------------------------
echo "$TAG [5/6] enabling postgresql service..."
ssh "$HOST" "lxc exec ${CONTAINER} -- systemctl enable --now postgresql"
ssh "$HOST" "lxc exec ${CONTAINER} -- systemctl restart postgresql"

# ---- 6. tailnet join ------------------------------------------------------
echo "$TAG [6/6] joining tailnet..."
ssh "$HOST" "lxc exec ${CONTAINER} -- bash -s" <<REMOTE
set -euo pipefail
if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sh
fi
tailscale up --authkey='${TAILSCALE_AUTHKEY}' --hostname='${CONTAINER}' --reset
REMOTE

TS_IP="$(ssh "$HOST" "lxc exec ${CONTAINER} -- tailscale ip -4" | head -n1)"

echo ""
echo "$TAG === ${CONTAINER} ready ==="
echo "$TAG tailnet IP:  ${TS_IP}"
echo "$TAG hostname:    ${CONTAINER}"
echo "$TAG DSN shape:   postgresql://capelle:<password>@${CONTAINER}:5432/capelle"
echo "$TAG next step:   set CAPELLE_POSTGRES_DSN on the yutas."

# Capelle Platform — Operations PLAYBOOK

Concise runbook for the day-to-day ops of the Capelle platform. For the
full historical v1.1 cutover narrative, see
[`PLAYBOOK_ARCHIVE_v1.md`](./PLAYBOOK_ARCHIVE_v1.md). For the deeper
architecture / auth audit, see [`INFRA_AUDIT.md`](./INFRA_AUDIT.md).

```
Cloudflare Tunnel ─► masamichi-yaga:8080 (nginx) ─► yuta-okkotsu:8080
                                                     yuta-maki-zenin:8080
                                                     yuta-<new>:8080 ...
                                                       │
                                                       ├── aoi-todo          (Postgres)
                                                       ├── kiyotaka-ijichi   (RabbitMQ)
                                                       └── yuji-itadori      (Chroma)
```

---

## 1. Daily deploys

One command, every time:

```bash
deploy/push.sh all
```

That builds the frontend, packs backend + frontend tarballs, pushes them to
every `RUNNING yuta-*` container on `gojo`, md5-verifies each drop, swaps the
new tree in atomically (old goes to `*.bak-<epoch>`), restarts
`capelle-platform.service`, and polls `/api/health` until 200. A single
failing yuta does not abort the others — the others stay healthy, the failed
one reports in the summary.

Variants:

| Command | Effect |
|---|---|
| `deploy/push.sh all` | Standard push: both components, restart, health. |
| `deploy/push.sh yuta-okkotsu` | Single target. |
| `deploy/push.sh all --component backend` | Backend only (no frontend rebuild). |
| `deploy/push.sh all --component frontend` | Frontend only. |
| `deploy/push.sh yuta-okkotsu --no-restart` | Swap files, leave service running on old code (useful for staged cutovers). |
| `deploy/push.sh all --dry-run` | Print the plan, touch nothing. |

The script is fail-fast: `set -euo pipefail`, md5 mismatch aborts that yuta
before extract, health failure aborts with non-zero exit. Backups older than
7 days are pruned after every successful deploy (newest 3 are always kept).

Why stdin-stream instead of `lxc file push`? INFRA_AUDIT noted that
`lxc file push` returns "Forbidden" on some LXD configurations; piping via
`lxc exec -- bash -c 'cat > …'` uses the same channel as an interactive
shell and is reliable across hosts.

---

## 2. Spin up a new yuta

One command from a brand-new LXC to healthy backend:

```bash
CAPELLE_TAILSCALE_AUTHKEY='tskey-auth-...' \
  deploy/bootstrap-yuta.sh
```

Picks the first unused JJK character name (`yuta-sukuna`, `yuta-megumi`, …),
launches `ubuntu:24.04`, installs the apt + tailscale baseline, copies the
`ohrs` binary from `yuta-okkotsu`, `snap install codex`, installs the systemd
unit + env template, deploys current code via `deploy/push.sh`, and runs
the codex-sync installer (`deploy/scripts/install-codex-sync.sh`).

Flags:

| Flag | Default | Meaning |
|---|---|---|
| `--name yuta-<char>` | first free from JJK pool | Explicit name. |
| `--image ubuntu:24.04` | `ubuntu:24.04` | Base image. |
| `--dry-run` | off | Print every step, execute none. |

Always dry-run first:

```bash
deploy/bootstrap-yuta.sh --dry-run
```

After bootstrap, **every new yuta needs its own `codex login --device-auth`**
(each yuta maintains an independent session — no shared tokens). The final
message from `bootstrap-yuta.sh` prints the exact command.

After bootstrap completes, add the new yuta to the nginx LB:

```bash
ssh gojo "lxc exec masamichi-yaga -- bash -c '
  sed -i \"/^[[:space:]]*# Append more yuta-/i\\\    server yuta-<name>:8080 max_fails=3 fail_timeout=30s;\" /etc/nginx/conf.d/capelle.conf
  nginx -t && systemctl reload nginx
'"
```

Or use the existing `scripts/add-to-lb.sh <slug>` wrapper.

Idempotency: if the container already exists, `bootstrap-yuta.sh` exits
non-zero with a clear error. Delete with `lxc delete -f <name>` and re-run.

---

## 3. Codex token refresh

**Per-yuta independent.** Every yuta has its own `codex login --device-auth`
session, its own snap `auth.json`, and its own 5-minute sync timer reading
from that snap into `/etc/capelle-codex.env`. No cross-yuta coupling.

Files:

- `deploy/systemd/capelle-codex-sync.service`
- `deploy/systemd/capelle-codex-sync.timer`
- `deploy/scripts/install-codex-sync.sh` (streamed into each yuta)
- `deploy/scripts/capelle-codex-sync.sh` (reference copy of the per-tick
  script; installer embeds this exact body as a heredoc)

Access tokens are JWTs valid for ~10 days. The sync script logs a WARN when
<24h remain so the admin dashboard's Codex-failures panel can surface a
"re-auth needed" signal before users see 401s.

**Do not edit `/etc/capelle-codex.env` by hand.** Any value you write gets
overwritten on the next timer tick. If the env file looks stale, check the
timer status:

```bash
ssh gojo "lxc exec yuta-okkotsu -- systemctl list-timers capelle-codex-sync.timer"
ssh gojo "lxc exec yuta-okkotsu -- journalctl -u capelle-codex-sync.service -n 50 --no-pager"
```

When you see `WARN access_token_near_expiry` or `FATAL auth_json_missing`,
re-auth on that specific yuta:

```bash
ssh gojo "lxc exec <yuta> -- snap run codex login --device-auth"
```

See `INFRA_AUDIT.md` §1–§4 for the full auth topology.

---

## 4. Diagnostics

### Health at each tier

```bash
# Public, via Cloudflare Tunnel
curl -s https://data-compass.org/api/health

# Via the nginx LB (tailnet)
ssh gojo "lxc exec masamichi-yaga -- curl -sf http://localhost:8080/api/health"

# Directly at a yuta (bypasses the LB)
ssh gojo "lxc exec yuta-okkotsu -- curl -sf http://127.0.0.1:8080/api/health"
```

### journalctl patterns

```bash
# Platform service on a yuta
ssh gojo "lxc exec yuta-okkotsu -- journalctl -u capelle-platform -n 100 --no-pager"

# Token sync on a yuta
ssh gojo "lxc exec yuta-okkotsu -- journalctl -u capelle-codex-sync -n 50 --no-pager"

# ChromaDB (per-yuta)
ssh gojo "lxc exec yuta-okkotsu -- journalctl -u chromadb -n 50 --no-pager"

# nginx on the LB
ssh gojo "lxc exec masamichi-yaga -- tail -n 100 /var/log/nginx/access.log"
ssh gojo "lxc exec masamichi-yaga -- tail -n 50 /var/log/nginx/error.log"
```

### "is my code actually deployed?"

```bash
# Compare md5 of a recently-changed backend file
md5sum backend/capelle_platform/main.py
ssh gojo "lxc exec yuta-okkotsu -- md5sum /opt/backend/capelle_platform/main.py"

# List backup rings (youngest last)
ssh gojo "lxc exec yuta-okkotsu -- bash -c 'ls -lt /opt/backend | head; ls -lt /opt/frontend | head'"
```

### RabbitMQ + Postgres reachability from a yuta

```bash
ssh gojo "lxc exec yuta-okkotsu -- bash -c '
apt-get install -y -qq netcat-openbsd >/dev/null 2>&1
nc -zv kiyotaka-ijichi 5672
nc -zv aoi-todo 5432
nc -zv yuji-itadori 8000
'"
```

### Run the push tests locally

```bash
bash deploy/scripts/test-push-sh.sh
```

---

## 5. Rollback

### Roll one yuta back to its previous code

```bash
ssh gojo "lxc exec yuta-okkotsu -- bash -c '
set -e
cd /opt/backend
latest_bak=$(ls -1dt capelle_platform.bak-* | head -1)
mv capelle_platform capelle_platform.rollback-$(date +%s)
mv \"\$latest_bak\" capelle_platform
systemctl restart capelle-platform
'"
```

Frontend rollback is the same pattern against `/opt/frontend`.

### Take a yuta out of rotation

```bash
ssh gojo "lxc exec masamichi-yaga -- bash -c '
sed -i \"s|^[[:space:]]*server yuta-<name>:8080.*|# \\0|\" /etc/nginx/conf.d/capelle.conf
nginx -t && systemctl reload nginx
'"
```

Put it back in by deleting the leading `# `.

---

## 6. Files in this directory

| Path | Owner | Purpose |
|---|---|---|
| `push.sh` | DEPLOY-UNIFY | Canonical code deploy. |
| `bootstrap-yuta.sh` | DEPLOY-UNIFY | Brand-new yuta from scratch. |
| `lib/push-lib.sh` | DEPLOY-UNIFY | Helper functions for `push.sh`. |
| `templates/capelle-platform.service` | DEPLOY-UNIFY | Systemd unit template. |
| `templates/capelle-codex.env.tmpl` | DEPLOY-UNIFY | Env file template (tokens blank). |
| `scripts/install-codex-sync.sh` | TOKEN-SYNC | Installs the 5-minute sync timer inside a yuta. |
| `scripts/capelle-codex-sync-{owner,follower}.sh` | TOKEN-SYNC | Per-role sync bodies. |
| `scripts/test-push-sh.sh` | DEPLOY-UNIFY | Shell-level tests for `push.sh`. |
| `systemd/capelle-codex-sync.{service,timer}` | TOKEN-SYNC | Timer unit + oneshot unit. |
| `INFRA_AUDIT.md` | INFRA | Current-state architecture audit (authoritative). |
| `PLAYBOOK_ARCHIVE_v1.md` | — | The pre-unification playbook; kept for history. |

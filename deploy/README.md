# Capelle Platform — Topology Runbook

In the Compass checkout, run the provisioners from `scripts/homelab/` (for
example, `bash scripts/homelab/provision-aoi-todo.sh`). The scripts preserve
the recorded `gojo` LXC topology and are intended for that homelab.

Bringing up the horizontally-scalable Capelle Data Assistent topology from
zero, in the correct order. Every container lives on host `gojo` and joins
the tailnet so services reach each other by hostname.

```
Cloudflare  ─►  masamichi-yaga  ─►  yuta-okkotsu        (template / first live)
(tunnel)        (nginx LB, 8080)    yuta-<slug>, ...    (spawned via spawn-yuta.sh)
                                         │
                                         ├─► aoi-todo            (Postgres 16)
                                         └─► kiyotaka-ijichi     (RabbitMQ)
```

Bring them up in this order: **aoi-todo → kiyotaka-ijichi → masamichi-yaga →
first yuta → cutover**. Each step is idempotent — re-running the provisioner
is a no-op once the container exists.

Always run provisioners from the workstation; they SSH into `gojo` themselves.

## 1. aoi-todo — shared Postgres

### Required env
| Var | Purpose |
| --- | --- |
| `CAPELLE_PG_PASSWORD` | password for the `capelle` Postgres role |
| `TAILSCALE_AUTHKEY`   | reusable Tailscale auth key |

### Command
```bash
CAPELLE_PG_PASSWORD='<pw>' \
TAILSCALE_AUTHKEY='tskey-auth-...' \
  bash scripts/provision-aoi-todo.sh
```

### Expected output
```
[provision-aoi-todo] === Provisioning aoi-todo on gojo ===
[provision-aoi-todo] [1/6] launching LXC container (ubuntu:24.04)...
... (install, role, hba, systemd, tailscale) ...
[provision-aoi-todo] === aoi-todo ready ===
[provision-aoi-todo] tailnet IP:  100.x.y.z
[provision-aoi-todo] DSN shape:   postgresql://capelle:<password>@aoi-todo:5432/capelle
```

Record the DSN shape — you feed this to the yutas as `CAPELLE_POSTGRES_DSN`.

## 2. kiyotaka-ijichi — shared RabbitMQ

### Required env
| Var | Purpose |
| --- | --- |
| `CAPELLE_RABBITMQ_PASSWORD` | password for the `capelle` RabbitMQ user |
| `TAILSCALE_AUTHKEY`         | reusable Tailscale auth key |

### Command
```bash
CAPELLE_RABBITMQ_PASSWORD='<pw>' \
TAILSCALE_AUTHKEY='tskey-auth-...' \
  bash scripts/provision-kiyotaka-ijichi.sh
```

### Expected output
```
[provision-kiyotaka-ijichi] === Provisioning kiyotaka-ijichi on gojo ===
...
[provision-kiyotaka-ijichi] === kiyotaka-ijichi ready ===
[provision-kiyotaka-ijichi] AMQP URL:    amqp://capelle:<password>@kiyotaka-ijichi:5672/capelle
[provision-kiyotaka-ijichi] Mgmt UI:     http://kiyotaka-ijichi:15672  (user: capelle)
```

Record the AMQP URL — you feed this to the yutas as `CAPELLE_RABBITMQ_URL`.

### 3b. Provision yuji-itadori (Chroma)

Until v1.1 every yuta ran its own in-process Chroma; then it was centralised
in `yuta-okkotsu`. This step pulls Chroma out of any yuta entirely so the
pool is symmetric — no yuta is special for holding the vector index.

#### Required env
| Var | Purpose |
| --- | --- |
| `TAILSCALE_AUTHKEY` | reusable Tailscale auth key |

#### Command
```bash
TAILSCALE_AUTHKEY='tskey-auth-...' \
  ./scripts/provision-yuji-itadori.sh
```

Then move the existing index off `yuta-okkotsu`. The migration stops
yuta-okkotsu's local `chromadb.service` and leaves it disabled — its data
directory is preserved as a rollback path, but traffic will no longer hit
it.

```bash
./scripts/migrate-chroma.sh           # preview what will run
./scripts/migrate-chroma.sh           # (omit --dry-run to execute)
```

The migration script supports `--dry-run`; use that first if you want to
see the exact lxc-exec tar pipeline before it runs.

#### Verify
```bash
curl http://yuji-itadori:8000/api/v2/heartbeat     # should return 200
```

#### Wire the yutas
After the migration succeeds, on **every** yuta append
`CAPELLE_CHROMA_HTTP=yuji-itadori:8000` to `/etc/capelle-codex.env` and
restart the service:

```bash
ssh gojo "lxc exec yuta-okkotsu -- bash -c '
  grep -q ^CAPELLE_CHROMA_HTTP= /etc/capelle-codex.env \
    && sed -i \"s|^CAPELLE_CHROMA_HTTP=.*|CAPELLE_CHROMA_HTTP=yuji-itadori:8000|\" /etc/capelle-codex.env \
    || echo CAPELLE_CHROMA_HTTP=yuji-itadori:8000 >> /etc/capelle-codex.env
  systemctl restart capelle-platform.service
'"
```

Repeat for each additional yuta (`yuta-maki-zenin`, etc.). Future yutas
spawned via `spawn-yuta.sh` inherit this setting — see step 4.

## 3. masamichi-yaga — nginx load balancer

At this point you want the LB pre-wired to the one yuta that's already
serving prod (`yuta-okkotsu`). The default seed is exactly that.

### Required env
| Var | Purpose |
| --- | --- |
| `TAILSCALE_AUTHKEY` | reusable Tailscale auth key |

### Optional env
| Var | Default | Purpose |
| --- | --- | --- |
| `YUTA_UPSTREAMS` | `yuta-okkotsu` | comma-separated yuta hostnames to seed |

### Command
```bash
TAILSCALE_AUTHKEY='tskey-auth-...' \
  bash scripts/provision-masamichi-yaga.sh
```

### Expected output
```
[provision-masamichi-yaga] seeding upstream with: yuta-okkotsu
...
[provision-masamichi-yaga] === masamichi-yaga ready ===
[provision-masamichi-yaga] listen:      http://masamichi-yaga:8080
[provision-masamichi-yaga] upstreams:   yuta-okkotsu
```

Smoke-test through the LB before cutover:
```bash
ssh gojo "lxc exec masamichi-yaga -- curl -s http://yuta-okkotsu:8080/api/health"
ssh gojo "lxc exec masamichi-yaga -- curl -s http://localhost:8080/api/health"
```
Both should return 200.

## 4. First yuta — usually you already have `yuta-okkotsu`

If you don't, run `bash scripts/deploy-lxc.sh yuta-okkotsu` to build the
gold image. Once it's up and serving traffic on `http://yuta-okkotsu:8080`,
skip ahead to step 5. Spawning additional yutas is done via:

```bash
CAPELLE_JWT_SECRET='<shared>' \
CAPELLE_POSTGRES_DSN='postgresql://capelle:<pw>@aoi-todo:5432/capelle' \
CAPELLE_RABBITMQ_URL='amqp://capelle:<pw>@kiyotaka-ijichi:5672/capelle' \
CAPELLE_CHROMA_HTTP='yuta-okkotsu:8000' \
CAPELLE_ADMIN_EMAILS='ops@example.com' \
CAPELLE_SUPPORT_EMAIL='support@example.com' \
OPENAI_API_KEY='sk-...' \
TAILSCALE_AUTHKEY='tskey-auth-...' \
  bash scripts/spawn-yuta.sh maki-zenin
```

This clones `yuta-okkotsu` into `yuta-maki-zenin`, points it at the shared
services, waits for `/api/health`, and registers it with `masamichi-yaga`.

Expect the script to print the updated upstream list at the end.

## 5. Cutover — swing Cloudflare Tunnel onto the LB

```bash
bash scripts/cutover-tunnel.sh           # preview only
bash scripts/cutover-tunnel.sh --yes     # apply
```

Expected last line:
```
[cutover-tunnel] Tunnel origin now → masamichi-yaga. Test with: curl https://data-compass.org/api/health
```

Then verify end-to-end:
```bash
curl https://data-compass.org/api/health    # should 200
```

If cloudflared is configured via the dashboard rather than a local
`config.yml`, the script will tell you so; flip the origin in the
Cloudflare dashboard's Public Hostnames page instead.

## Teardown

Use `scripts/offboard-lxc.sh <container>` to destroy any container cleanly.

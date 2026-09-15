# Compass deployment and operations

This runbook records how Compass was deployed at `data-compass.org` and explains
the requirements for deploying this checkout. The infrastructure details come
from the 2026 production architecture and deployment tooling. They have not been
verified against live servers as part of this documentation update.

The repository includes the application, local setup, data scripts, the
bundled research runtime in `agent/` (see its [inventory](../agent/README.md)),
and the recorded homelab deployment tooling under `deploy/` and
`scripts/homelab/`. These scripts retain the original `gojo`/LXC topology and
must be run from a workstation with the corresponding SSH and container access.

See [architecture](../ARCHITECTURE.md) for component responsibilities and data
flows, and [runtime setup](runtime.md) for agent prerequisites.

## Recorded infrastructure

| Host/container | Service | Endpoint or location |
| --- | --- | --- |
| `gojo` | Physical LXC host and SSH deployment target | `ssh gojo` |
| `masamichi-yaga` | Cloudflare connector and nginx ingress | nginx `:8080` |
| `yuta-okkotsu` | Application replica and provisioning template | FastAPI `:8080` |
| `yuta-maki-zenin` | Application replica | FastAPI `:8080` |
| `aoi-todo` | Postgres 16 | `:5432` |
| `kiyotaka-ijichi` | RabbitMQ | AMQP `:5672`, management `:15672` |
| `yuji-itadori` | ChromaDB | HTTP `:8000` |

The public request path is:

```text
data-compass.org
  -> Cloudflare edge and tunnel
  -> cloudflared on masamichi-yaga
  -> nginx on localhost:8080
  -> yuta application pool on port 8080
```

Cloudflare supplies public TLS. The tunnel's public hostname points to the
ingress service. In the recorded arrangement the connector and nginx run in
the same container, so the origin can be `http://localhost:8080`. For a remotely
managed tunnel, its public hostname and DNS are configured in Cloudflare; a
locally managed tunnel uses its installed configuration.

The containers use Tailscale/private networking. Resolve the service hostnames
from each container before configuring upstreams or database connections.

## Production files and service

The recorded installation uses this layout on each application replica:

```text
/opt/backend/capelle_platform/    Python application package
/opt/backend/.venv/              Installed Python environment
/opt/frontend/dist/              Built Compass frontend
/etc/capelle-codex.env            Service environment and secrets
/usr/local/bin/ohrs              Installed agent runtime
```

This layout matches the current static frontend lookup when the package is
installed at `/opt/backend/capelle_platform`: it resolves `/opt/frontend/dist`.
For a checkout deployed elsewhere, keep `backend/` and `frontend/` as siblings.
Build the frontend before starting the backend, since static routing is
registered at application startup only when the build directory exists.

The systemd service is named `capelle-platform.service`. Its essential execution
contract is:

```ini
[Service]
WorkingDirectory=/opt/backend
EnvironmentFile=/etc/capelle-codex.env
ExecStart=/opt/backend/.venv/bin/python -m capelle_platform.main
Restart=on-failure
RestartSec=5
```

This is a service excerpt, not a complete installer. The historical provisioner
installs the unit and an environment-file drop-in. Select a service account with
access to the configured tools and data and write access to the job workspace.
Keep the environment file restricted; the recorded permissions are `0600`.

## Configuration

All application replicas need compatible application versions, runtime assets,
database settings, broker settings, and signing secrets. A multi-replica setup
must explicitly select the production transports:

```dotenv
CAPELLE_HOST=0.0.0.0
CAPELLE_PORT=8080
CAPELLE_DEBUG=false
CAPELLE_PUBLIC_ORIGIN=https://data-compass.org
CAPELLE_AUTH_COOKIE_SECURE=true
CAPELLE_STORE=postgres
CAPELLE_POSTGRES_DSN=postgresql://capelle:<password>@aoi-todo:5432/capelle
CAPELLE_QUEUE=rabbitmq
CAPELLE_WS_HUB=rabbitmq
CAPELLE_RABBITMQ_URL=amqp://capelle:<password>@kiyotaka-ijichi:5672/capelle
CAPELLE_CHROMA_HTTP=yuji-itadori:8000
CAPELLE_WORKER_CONCURRENCY=5
CAPELLE_JWT_SECRET=<strong-secret-identical-across-replicas>
CAPELLE_INTERNAL_HOOK_SECRET=<strong-hook-secret>
CAPELLE_TRUSTED_PROXIES=<actual-trusted-proxy-addresses-or-networks>
CAPELLE_ADMIN_EMAILS=<operator-email-addresses>
```

Replace placeholders through the deployment's secret configuration. Use the
dedicated `capelle` RabbitMQ virtual host with the intended credentials and
permissions. Supplying DSN/URL values alone does not select the production
transports: the historical spawn script otherwise defaults to SQLite and memory.

Configure the analysis runtime separately:

| Setting | Purpose / current checkout default |
| --- | --- |
| `CAPELLE_OHRS_BINARY` | Actual installed OHRS executable |
| `CAPELLE_OHRS_WORKING_DIR` | Municipal workspace; `/opt/compass/agent-workspace` |
| `CAPELLE_OHRS_WORKING_DIR_JEUGDZORG` | Youth-care workspace; `/opt/compass/jeugdzorg-workspace` |
| `CAPELLE_OHRS_TOOLS_BIN` | CLI directory; `/opt/compass/tools/bin` |
| `CAPELLE_OHRS_HOME_DIR` | Provider configuration and caches; `/opt/compass/agent-home` |
| `CAPELLE_OHRS_JOBS_DIR` | Writable job artifacts; `/tmp/compass-jobs` |

The bundled runtime can be staged at `/opt/compass/agent`, with youth-care at
`/opt/compass/agent/jeugdzorg` and the installed tools in the release
environment. Set explicit absolute paths for all runtime settings.

Historical installations used `/opt/capelle-repo-agent` for the municipal
workspace and `/opt/tools-venv/bin` for CLIs. Preserve those explicit overrides
when updating an existing installation, or stage the assets at the configured
new paths. Provider credentials/configuration must work under the service's
agent HOME. The operational tooling supports provider tokens or an API key;
credential availability must be checked independently of web-server health.

Enable email delivery, verification, or Entra sign-in only with their required
provider and callback configuration. These optional integrations are described
by the settings in `backend/capelle_platform/settings.py`.

## Provisioning the recorded topology

Run the historical operational tooling on a workstation with SSH access to
`gojo` and permission to manage its LXC containers. Load the required credentials
into the workstation environment before invoking provisioners.

1. Provision `aoi-todo` using `scripts/homelab/provision-aoi-todo.sh` with
   `CAPELLE_PG_PASSWORD` and `TAILSCALE_AUTHKEY`.
2. Provision `kiyotaka-ijichi` using `scripts/homelab/provision-kiyotaka-ijichi.sh` with
   `CAPELLE_RABBITMQ_PASSWORD` and `TAILSCALE_AUTHKEY`.
3. Provision `yuji-itadori` using `scripts/homelab/provision-yuji-itadori.sh` and populate
   its vector index. For an existing index on the first application replica,
   preview `scripts/homelab/migrate-chroma.sh --dry-run` before running the migration.
   The migration stops both Chroma services during transfer, retains the source
   data, and disables the source service after the target responds successfully.
4. Create or prepare `yuta-okkotsu` with `scripts/homelab/deploy-lxc.sh yuta-okkotsu`.
   Install the application dependencies, frontend, agent runtime, skills, tools,
   and data. Apply the production settings above and check database migrations
   before enabling service traffic.
5. Provision `masamichi-yaga` with `scripts/homelab/provision-masamichi-yaga.sh`, setting
   `YUTA_UPSTREAMS=yuta-okkotsu` and the required Tailscale/tunnel credentials.
   Confirm nginx can reach the first replica before routing public traffic.
6. Add the second application replica using `scripts/homelab/spawn-yuta.sh maki-zenin`,
   with the production transport selectors and all required credentials exported.
7. Configure the Cloudflare tunnel and public hostname to reach the ingress.
   For the historical local-config cutover, `scripts/homelab/cutover-tunnel.sh` previews
   changes and `scripts/homelab/cutover-tunnel.sh --yes` applies them. Remotely managed
   origins are changed in the tunnel dashboard.
8. Run the verification sequence below and complete a real analysis.

Provisioning scripts have different rerun behavior: some skip existing
containers, while the spawn script refuses an existing target. Review the
individual script before using it for an already provisioned host.

## How code updates were deployed

Deployment was initiated manually from the workstation's working tree. The
canonical operational command was:

```bash
# From the Compass checkout
deploy/push.sh all --dry-run
deploy/push.sh all
```

The script uses `CAPELLE_LXC_HOST`, defaulting to `gojo`. `all` discovers running
containers named `yuta-*` on that host. Inspect the dry-run target list; explicit
container names are available when a narrower rollout is required.

The delivery sequence is:

1. Build `frontend/dist` with `npm run build`.
2. Package `backend/capelle_platform` and the frontend into separate tarballs.
3. Stream artifacts over SSH to `gojo`, then through `lxc exec` into each target.
4. Compare local and remote MD5 checksums to detect transfer corruption.
5. Extract into sibling staging directories, rename the previous live directories
   to timestamped backups, and move the staged directories into place.
6. Restart `capelle-platform.service` on the target.
7. Poll `/api/health` for HTTP 200, with 30 attempts and a two-second interval.
8. After success, prune backups older than seven days while retaining the newest
   three backups per component.

Directory renames reduce the exposure to partially extracted files, but the
backend/frontend swaps are separate operations and the fleet update is not a
transaction. The script does not drain jobs or guarantee zero downtime.
Failures can leave a partially updated fleet and do not automatically restore
previous versions.

Useful command variants in the operational tooling are:

```bash
deploy/push.sh yuta-okkotsu
deploy/push.sh yuta-maki-zenin --component backend
deploy/push.sh yuta-okkotsu --component frontend
```

`--no-restart` also skips the script's health check. Use it only as part of a
planned operation that performs the necessary restart and verification later.

The push script builds and packages the checkout containing the script. Calling
an old deployment script from the Compass directory does not change its source
root. To deliver this checkout, the deployment packaging must explicitly use
this checkout's `backend/` and `frontend/dist/`.

The code push does not install changed Python requirements, rotate secrets,
provision services, synchronize corpora, install agent tools, or orchestrate a
database migration. Handle those dependencies as explicit release steps.

## Preparing a release from this checkout

Use the repository's supported installation and validation commands:

```bash
make install
make test
make build
```

These require the Python and Node versions listed in the README. Package the
application code and compiled frontend with the deployment layout above. Install
the required Python dependencies in the destination environment. Stage the
runtime prerequisites and configure database/broker services before starting
`python -m capelle_platform.main` under the process manager.

Review schema changes and queued work before deploying. Use a compatible
application/schema rollout, take the necessary backups, and allow active jobs
to finish or handle their interruption deliberately. A successful build or an
HTTP health response alone does not verify the research tools and provider.

## Scaling the application pool

The historical `scripts/homelab/spawn-yuta.sh <slug>` clones `yuta-okkotsu`, assigns the
new container its hostname, replaces the copied Tailscale identity, writes its
service environment, checks health, and registers it with nginx. It stops the
template while copying, so schedule the resulting interruption. The script
already performs load-balancer registration; a second add-to-LB invocation is
not part of the normal sequence.

`HOLD_FROM_LB=1` keeps a new replica out of public HTTP traffic until it is ready.
Starting its application service can still connect workers to RabbitMQ; being
absent from nginx does not isolate it from queued production jobs.

Every replica needs the correct data and tools. Cloning a stale template
reproduces its stale assets. More replicas increase worker capacity but all
containers still share the physical host and central data-service capacity.

## Verification and monitoring

Run these read-only checks from the workstation or a host with the required
private network access:

```bash
# Application replica
ssh gojo 'lxc exec yuta-okkotsu -- curl --fail --silent --show-error http://127.0.0.1:8080/api/health'

# nginx ingress
ssh gojo 'lxc exec masamichi-yaga -- curl --fail --silent --show-error http://127.0.0.1:8080/api/health'

# Public path
curl --fail --silent --show-error https://data-compass.org/api/health

# Database
ssh gojo 'lxc exec aoi-todo -- sudo -u postgres psql -d capelle -c "SELECT 1"'

# Broker backlog and consumers
ssh gojo 'lxc exec kiyotaka-ijichi -- rabbitmqctl list_queues -p capelle name messages_ready messages_unacknowledged consumers'

# Vector service from an application replica
ssh gojo 'lxc exec yuta-okkotsu -- curl --fail --silent --show-error http://yuji-itadori:8000/api/v2/heartbeat'

# Recent application logs
ssh gojo 'lxc exec yuta-okkotsu -- journalctl -u capelle-platform.service -n 100 --no-pager'
```

Repeat replica checks for each target. Verify the public frontend loads,
authentication works, and a real analysis completes with references and expected
graph/report output. Exercise live progress and a clarification response across
replicas. Check that completed results survive refresh and share links resolve.

The optional `CAPELLE_EXPOSE_HOSTNAME_HEADER=true` setting enables response
attribution through `X-Capelle-Hostname` when needed to verify replica routing.
Use nginx logs in `/var/log/nginx/capelle.access.log` and
`/var/log/nginx/capelle.error.log`, and `journalctl -u cloudflared` inside ingress
for tunnel diagnosis.

Monitor dead-letter jobs, queue depth, unacknowledged work, dependency health,
provider failures, disk usage, and host capacity. The original record noted
missing off-host backups, external uptime alerts, and centralized logging;
their current status needs a separate operational check.

## Recovery and rollback

For a failed code release, identify the affected replicas and retain their logs.
Stop or drain relevant work, restore a compatible backend/frontend backup pair
from the timestamped directories, restart the service, and rerun health and
functional checks. Check the whole target list because earlier replicas may
already have updated successfully.

Code backups do not restore the Python environment, database schema or contents,
broker state, secrets, or research data. A schema-changing release needs its own
recovery plan and database backup. Restoring old application files against an
incompatible schema is not sufficient.

SQLite and memory transports remain useful for local development. Switching a
production replica to them does not recover current Postgres state or RabbitMQ
jobs and can create divergent application state. Historical SQLite files are
snapshots, not a current production failover database.

Maintain and test off-host Postgres backups, preserve or reproducibly rebuild
the vector index and corpora, and document restoration of the service environment
and runtime assets. Loss of `gojo` affects the entire recorded deployment.

## Documentation basis

This runbook consolidates the production architecture record, topology runbook,
nginx configuration, code-push script and helper library, LXC provisioners,
replica-spawn script, Chroma migration script, and tunnel-cutover procedure.
Current paths and configuration were checked against this repository's
`static_mount.py`, `settings.py`, `main.py`, Makefile, and runtime documentation.
No live infrastructure changes or production validation were performed.

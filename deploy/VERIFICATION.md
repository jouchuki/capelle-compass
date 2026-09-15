# v1.1 Verification Runbook

`scripts/verify-v1.1.sh` is the end-to-end harness. Run it AFTER the v1.1
cutover — i.e. after both yutas are on the v1.1 code and both are in the
nginx upstream block on `masamichi-yaga`.

## Prerequisites

- SSH access to `gojo` (for `lxc exec` into yutas, aoi-todo, yuji-itadori).
- A workstation with `curl`, `python3` (>=3.11), and the `websockets`
  Python package (`pip install websockets`).
- Ops admin credentials exported:
  ```
  export CAPELLE_ADMIN_EMAIL='v.sokolovs@outlook.com'
  export CAPELLE_ADMIN_PASSWORD='…'
  ```

## Running

```
./scripts/verify-v1.1.sh
```

Optional env overrides:
- `PUBLIC_ORIGIN` (default `https://data-compass.org`)
- `HOST` (default `gojo`)
- `YUTAS` (default `"yuta-okkotsu yuta-maki-zenin"`)
- `CHROMA_CONTAINER` (default `yuji-itadori`)
- `POSTGRES_CONTAINER` (default `aoi-todo`)

Exit code `0` = GO, `1` = NO-GO.

## Reading the output

Each check prints one line in the form
`[<id>] PASS|FAIL — <short note>`, followed by a bottom-line
`GO` or `NO-GO`.

### (a) LB routes to both yutas
Ten hits against `/api/health`. Reads the `X-Capelle-Hostname` response
header set by the `_stamp_hostname` middleware in
`backend/capelle_platform/builder.py`. At least two distinct hostnames
must appear.

- **FAIL `only 1 distinct hostname`** → one yuta is not in the upstream,
  or nginx isn't rotating. Check `ssh gojo "lxc exec masamichi-yaga -- awk
  '/^upstream capelle_yutas/,/^}/' /etc/nginx/conf.d/capelle.conf"`.
- **FAIL on status** → the LB itself isn't healthy. Check
  `ssh gojo "lxc exec masamichi-yaga -- journalctl -u nginx -n 50"`.

### (b) Cross-yuta Postgres
Registers a throwaway user via the LB, then SELECTs it from `aoi-todo`.

- **FAIL `register returned status=…`** → an HTTP-level failure. Check
  the hit yuta's logs: `ssh gojo "lxc exec yuta-okkotsu -- journalctl -u
  capelle-platform -n 100"` (and again for `yuta-maki-zenin`).
- **FAIL `NOT found in aoi-todo`** → the yuta wrote to the wrong store.
  Inspect `CAPELLE_POSTGRES_DSN` on each yuta with
  `ssh gojo "lxc exec yuta-<slug> -- cat /etc/capelle-codex.env"`.

### (c) Cross-yuta RabbitMQ queue
Submits 4 analysis messages, waits 60s, then counts `job_completed`
log lines per yuta. Both yutas must drain at least one.

- If one yuta monopolises a single run, the harness tries once more
  before failing (round-robin can legitimately be unlucky with n=4).
- **FAIL `2nd run still monopolised`** → one yuta's worker is not
  subscribed to the shared queue. Inspect
  `ssh gojo "lxc exec yuta-<slug> -- journalctl -u capelle-platform -n
  200 | grep -iE 'rabbit|consumer|queue'"`. Also check RabbitMQ itself:
  `ssh gojo "lxc exec kiyotaka-ijichi -- rabbitmqctl list_consumers"`
  should list BOTH yutas as consumers on `capelle.jobs`.

### (d) Cross-yuta WS fanout
**The** test that proves the broker-backed WS hub works. Opens a WS
to the LB (sticking to one yuta), submits 6 messages (very high chance
at least one lands on the OTHER yuta), and waits up to 200s for a
`message_complete` event on the WS.

- **FAIL `websockets-missing`** → `pip install websockets` on the
  workstation.
- **FAIL `no message_complete seen on WS within timeout`** → the WS
  fanout is broken. Typical culprits:
  - The `capelle.ws` exchange isn't declared on `kiyotaka-ijichi`:
    `ssh gojo "lxc exec kiyotaka-ijichi -- rabbitmqctl list_exchanges"`.
  - The other yuta is publishing to the wrong broker (check its
    `CAPELLE_RABBITMQ_URL`).
  - The WS-side yuta isn't consuming the fanout queue (grep the yuta
    logs for `ws_hub_connected` / `ws_hub_fanout`).
- **FAIL `no job drained on the OTHER yuta`** → fanout didn't get
  exercised; the LB may be sticky. Re-run and, if still one-sided,
  inspect nginx's `upstream_addr` in
  `ssh gojo "lxc exec masamichi-yaga -- tail -n 200
  /var/log/nginx/capelle.access.log"`.

### (e) Chroma reachable from both yutas
`curl http://yuji-itadori:8000/api/v2/heartbeat` from inside each yuta.

- **FAIL `yuta-…=000`** → tailnet / DNS issue inside that container.
  Confirm with
  `ssh gojo "lxc exec yuta-<slug> -- getent hosts yuji-itadori"`.
- **FAIL `yuta-…=404`** → Chroma is up on a different path (v1 vs v2).
  Sanity-check the URL directly: `ssh gojo "lxc exec yuji-itadori --
  curl -sS http://127.0.0.1:8000/api/v2/heartbeat"`.

### (f) Token accounting still flows
Hits `/api/admin/stats/tokens?days=1&limit=5` as the ops admin.

- **FAIL `parse_error`** → handler returned HTML (likely a 401/500
  page). Re-check the admin cookie in
  `ssh gojo "lxc exec yuta-okkotsu -- journalctl -u capelle-platform
  -n 50 | grep -i token"`.
- **FAIL `empty`** → token-accounting wasn't exercised during the run.
  Usually means the probe submissions in (c)/(d) didn't finish. Re-run
  verify-v1.1.sh once more after giving the queue time to drain.

## Investigation cheat-sheet

| symptom                                 | first command                                                          |
| --------------------------------------- | ---------------------------------------------------------------------- |
| any yuta unhealthy                      | `ssh gojo "lxc exec yuta-<slug> -- journalctl -u capelle-platform -n 200"` |
| nginx upstream misconfigured            | `ssh gojo "lxc exec masamichi-yaga -- nginx -T \| grep -A5 capelle_yutas"` |
| RabbitMQ consumers missing              | `ssh gojo "lxc exec kiyotaka-ijichi -- rabbitmqctl list_consumers"`    |
| Postgres writes missing                 | `ssh gojo "lxc exec aoi-todo -- psql -U capelle -d capelle -c '\\dt'"` |
| Chroma unreachable                      | `ssh gojo "lxc exec yuji-itadori -- journalctl -u chromadb -n 100"`    |

When all six checks report PASS and the harness exits `0 / GO`, v1.1
scale-out is verified end-to-end.

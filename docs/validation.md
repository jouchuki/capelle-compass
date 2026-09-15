# Validation

- Backend: 328 tests passed; 6 environment-dependent tests skipped.
- Frontend: 127 tests passed.
- Data scripts: 19 tests passed.
- Frontend production build: passed, with a bundle-size warning.
- Application startup, account creation, session listing through proxy aliases,
  request validation, and static-file containment: passed.

Backend checks used the workstation Python environment. Live analysis,
Postgres/RabbitMQ integration, email delivery, Entra login, and production
deployment were not exercised. See [runtime setup](runtime.md) for prerequisites.

## Bundled research runtime (2026-09-09)

Verification used an isolated temporary copy of the Compass checkout.

- Fresh installation of all eight agent Python projects and optional dashboard/refresh dependencies in an isolated temporary checkout: passed; `pip check` found no broken requirements.
- Both real skill workspaces and all ten installed CLI entry points: passed.
- Backend plugin, mode-budget, and tool-path tests: 16 passed, including two tests against the bundled workspaces.
- Copied graph CLI and municipal-corpus tests: 18 passed. Graph tests used a local HTTP stub.
- Offline CBS search/geography, budget and citizen-report queries, youth-care coverage/playbook, cube schema inference, and corpus coverage: passed from an unrelated working directory with an isolated HOME.
- Both auxiliary dashboard production builds: passed. The municipal dashboard emitted an existing mixed static/dynamic import warning.
- Auxiliary Flask API checks: five endpoints passed, including policy queries using the bundled JSON snapshot.
- Local configuration generation, private file permissions, and refusal to overwrite existing configuration: passed in a relocated checkout.
- Imported Python syntax, excluded-product references, credential patterns, and symlink containment: checked.

No live model analysis, embedding generation, national corpus download, public
deployment, or production service change was performed. Corpus coverage can be
empty until the national data is populated; the installation check does not
assert source completeness. Virtual environments and node_modules used for
verification are not copied into the source bundle.

Large generated catalogs, budget/report exports, policy snapshots, downloaded
survey PDFs, and the local Chroma index were removed after verification. Their
ignore rules and regeneration instructions are documented in
`agent/README.md`. The final Git-visible source footprint is approximately 8 MiB;
no retained file exceeds 50 MiB.

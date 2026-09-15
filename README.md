# Compass

Municipal research with source-backed reports, finding graphs, and the groeikern and jeugdzorg analysis modes.

The application includes a React frontend, FastAPI backend, tests, data scripts,
and configuration.

## Frontend demonstration

Requires Node.js 22 and npm. From this repository:

```sh
make install-frontend
make demo
```

Open http://localhost:5173. Demo mode uses built-in sample responses and needs
no backend, accounts, credentials, or agent runtime. It does not run real analyses.

## Full local application

Requires Python 3.12, Node.js 22, and npm.

```sh
make install
make init-local
make backend
```

In a second terminal, run `make dev` and open http://localhost:5173.
The Vite proxy targets this repository's backend on port 8080.
`make init-local` creates `backend/.env` with fresh local secrets and refuses to
overwrite existing configuration. SQLite, the in-memory queue, and the in-memory
WebSocket hub are used locally. Accounts are created in this repository's database.

Research tools, skills, and reference snapshots are bundled in `agent/`. Run
`make install-agent` and `make check-agent` to install and verify them. Real
analysis additionally requires OHRS, provider configuration, and the needed
external data/indexes. See [runtime setup](docs/runtime.md).

## Architecture and deployment

See [architecture](ARCHITECTURE.md) for the application, research runtime,
production topology, and data flows. The [deployment runbook](docs/deployment.md)
records the LXC infrastructure, code delivery process, configuration,
verification, scaling, and recovery procedures.

## Checks

```sh
make test
make build
```

`make test` runs backend and frontend suites; `make build` type-checks and builds
the frontend. The backend serves `frontend/dist` from this checkout after a build.

## Layout

- `frontend/`: React, TypeScript, Vite, and UI tests.
- `backend/`: FastAPI application and tests.
- `scripts/`: data preparation, runtime download, and local setup.
- `data/`: municipal reference metadata; large corpora are downloaded separately.
- `agent/`: research applications, ten CLI commands, skills, and reference snapshots.
- `ARCHITECTURE.md`: application architecture and recorded production topology.
- `docs/deployment.md`: production deployment and operations.
- `docs/runtime.md`: local configuration and agent prerequisites.

The backend uses the Python package `capelle_platform` and the `CAPELLE_`
environment-variable prefix. See [validation](docs/validation.md) for check results.

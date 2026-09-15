# Compass research runtime

This directory contains the research applications, CLI tools, skills, and
reference assets used by Compass. It is self-contained source: no original
tool repository or workstation-specific symlink is needed to install it.

From the Compass repository root:

```bash
make install-agent
make check-agent
```

The installer requires Python 3.12+ and package-registry access. It creates
`.venv-agent/` and installs the eight Python projects below in editable mode.
Keep `agent/` with that environment: several tools intentionally read their
adjacent reference files and helper modules. `check-agent` checks both workspace
layouts and launches all ten CLI entry points from an unrelated directory.

## Included tools and applications

| Directory | Installed command / purpose |
| --- | --- |
| `cbs_tool/` | `cbs`: CBS table discovery, geographic lookup, retrieval, and semantic-search utilities |
| `Capelle_beleid/` | `capelle-beleid`: policy retrieval, index building, and source-URL resolution |
| `Capelle_budget/` | `capelle-budget`: budget queries and refresh pipeline |
| `Capelle_buitenbeter/` | `capelle-buitenbeter`: citizen-report queries and refresh pipeline |
| `Capelle_cube/` | `capelle-cube`: schema inference and eleven analytical probes over CSVs |
| `Capelle_groeikernen/` | `groeikernen`: municipal corpus search and coverage |
| `Capelle_report/` | `capelle-report`, `capelle-ask`, `capelle-graph`: structured reports and platform callbacks |
| `jeugdzorg/` | `capelle-jeugdzorg`: youth-care probes and forecasting over synthetic CSVs |
| `capelle_rag/` | Shared structured-output schema and output helper |
| `Capelle_begrotingen/` | Policy-PDF acquisition script |
| `Capelle_enquetes/` | Resident-survey acquisition scripts; PDFs are downloaded separately |
| `dashboard/` | Auxiliary viewer for locally saved analysis/tool outputs |
| `Capelle_dashboard/` | Auxiliary municipal indicators and policy dashboard |

`skills/` contains eleven municipal analysis playbooks. The separate
`jeugdzorg/skills/` directory supplies the youth-care playbook. Each workspace has
an OHRS plugin manifest and relative link to its own skills. Workspace settings
carry tool permissions and memory settings; provider/model configuration is
supplied by the operator. The backend creates the per-job plugin and HTTP hooks.

## Reference data and GitHub footprint

The retained source bundle is approximately 8 MiB before installation. It keeps
the municipality mapping, tool source, skills, dashboard code, and three
explicitly synthetic youth-care CSVs. Generated catalogs, budget and citizen
report exports, policy JSON snapshots, downloaded survey PDFs, Chroma indexes,
database files, virtual environments, node_modules, provider secrets, and local
operator configuration are excluded from Git.

The national CVDR, bekendmakingen, and Iv3 corpora live in Compass's
`data/groeikernen/`. `agent/data` is a relative link to the repository's `data/`
directory. Populate these corpora with the data scripts in the repository or a
prepared data snapshot. Policy-PDF indexing uses `data/policy-pdfs/`, overridable
with `CAPELLE_POLICY_PDF_ROOT`. Chroma indexes must be built or restored separately.

`CBS_DATA_DIR` overrides the bundled CBS metadata directory when running the
CLI directly. `GROEIKERNEN_DATA` overrides corpus discovery; the backend passes
the configured municipal workspace's data path into its jobs. Chroma clients
use `CAPELLE_CHROMA_HTTP` when supplied, or a local index under
`agent/capelle_rag/chroma_db/` (`CAPELLE_CHROMA_PATH` can override it for direct CLI
use).

## Auxiliary dashboards and data refresh

Install their additional Python dependencies with:

```bash
make install-agent-extras
```

For browser-based scrapers, also install the Playwright browser and its required
system libraries on the machine where scraping runs:

```bash
.venv-agent/bin/python -m playwright install chromium
```

Each auxiliary dashboard has its own npm manifest and lockfile. Run these in the
chosen dashboard directory, using separate terminals for its API and frontend:

```bash
# From agent/Capelle_dashboard/
../../.venv-agent/bin/python scripts/api.py
npm ci
npm run dev
```

For `agent/dashboard/`, use `../../.venv-agent/bin/python api.py` instead.
The APIs use port 5000, so run one at a time. These are local operator utilities;
Compass's main frontend remains in the top-level `frontend/` directory. The
municipal dashboard can read a provisioned policy JSON without a crawler database.
The analysis viewer reads `agent/capelle_rag/analyses/`; it starts empty and is
independent of the backend's persisted session database.

Data downloads, embedding/index builds, browser installs, and model calls are
explicit operator actions. Neither the installer nor `check-agent` launches
them. See [runtime setup](../docs/runtime.md) before running real analyses.

## Source provenance and integration

The municipal source and skills were copied from the local
`capelle-repo-agent` workspace on 2026-09-09. The youth-care CLI, playbook, and
synthetic CSVs came from the local `capelle-jeugdzorg` workspace because that mode
requires them. The retained municipality metadata came from the existing CBS
tool's local reference-data directory. Large generated snapshots were removed
after import to keep the repository suitable for GitHub.

The imported source includes required files present on disk even where the
source repository did not track them, such as youth-care probe/schema modules,
citizen-report refresh scripts, and dashboard data modules. Compass adaptations
replace workstation-specific paths, preserve source-relative resource lookup,
and connect both analysis modes to the installed tools. Credential-bearing
configuration and source deployment scripts were not copied.

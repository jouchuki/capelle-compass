---
name: cube
description: Run an 11-probe OLAP-style analytical playbook against ANY tabular CSV with (time × dimensions × metrics) shape — surfaces trends, share shifts, interactions, unit prices, quality outliers, concentration, anomalies, and more. Schema is auto-inferred so column names don't matter.
argument-hint: <path to CSV file or short description of the question>
---

`cube` runs a fixed analytical playbook (11 typed probes) against any tabular CSV. It is the right tool whenever you have an OLAP-shaped dataset and need a structured analytical view in one call — not for text corpora, not for graph data, not for single-KPI time series.

## When to use

**USE `cube` when:**
- The data is a CSV (or other tabular file) with at minimum:
  - one **time** column (year-ish, monotonic, ≤ ~50 distinct values)
  - one or more **categorical dimensions** (2-50 distinct values each)
  - one or more **numeric metrics** (summable amounts or counts)
- You need a comprehensive analytical scan — trends, share shifts, interactions, anomalies, concentration — in one call
- Examples: budget tables, claims/declarations, demographic counts by wijk/year, complaint volumes by category, subsidie-uitkeringen, OZB-opbrengsten, anything (entity × time × amount)-shaped

**DO NOT use it for:**
- Text corpora — use `beleid search` or `groeikernen search` for verordeningen, beleidsdocumenten, bekendmakingen
- Bewonersenquete PDFs — use `pdftotext` per the bewonersenquete skill
- Single-KPI time series with no categorical dim — most probes degenerate
- Looking up one specific number — read the `./groeikernen_iv3/` CSVs or `cbs get` directly
- Network/graph data (referral chains, citation graphs, etc.)

## How it works

`cube` infers the role of each column automatically (time / dim / metric / client-id / ratio-pair / price-unit / age-bin), so you don't need to tell it your schema. Then it runs 11 probes and returns typed `Finding`-objects ranked by `|magnitude|`.

## Commands

```bash
cube playbook --csv <path>                  # COMPACT: top-8 findings per probe, no evidence (~25 KB JSON)
cube playbook --csv <path> --full           # FULL: every finding + evidence (~95 KB, 4x more tokens)
cube probe <name> --csv <path>              # ONE probe, FULL output — drill for evidence
cube probe <name> --csv <path> --compact    # ONE probe, compact view (rare)
cube infer-schema --csv <path>              # Print auto-inferred role assignments
```

If schema inference picks the wrong role for a column, override:
```bash
cube infer-schema --csv X > schema.json
# edit schema.json by hand (e.g., move a numeric code from metrics to dims)
cube playbook --csv X --config schema.json
```

## The 11 probes

| probe | what it measures | needs |
|---|---|---|
| `trajectory` | Yearly totals + OLS slope per metric, YoY step-changes | time + ≥1 metric |
| `share_drift` | Per-dim-value share of year total + slope (pp/yr) | ≥1 dim + ≥1 metric |
| `interactions` | Sarawagi joint-cube residuals (2-way and 3-way) | ≥2 dims + ≥1 metric |
| `unit_price` | Intra-provider geographic price spreads + YoY price jumps | ≥2 metrics (price = amount/qty) + ≥2 dims |
| `quality_ranking` | Per-provider absolute ratio + outliers >2pp under mean | ratio pair (num ⊂ den) + provider-dim |
| `coverage` | Dim values present in <60% of another dim's values | ≥2 dims |
| `concentration` | Pareto top-X% / service-stacking / per-wijk HHI | client-id col + ≥1 dim |
| `anomaly_scan` | Negative-value batches + row-level z-outliers | ≥1 additive metric |
| `sub_annual` | Monthly z-anomalies + yearly category-vs-population gaps | date column finer than year |
| `relative_pricing` | Volume-weighted price multiplier vs population median | ≥2 metrics + price-unit-dim |
| `age_alignment` | Avg age + dominant Leeftijd_band per categorie | age-binnable numeric col |

Each finding has `pattern_type`, `label`, `description`, `magnitude`. The `description` already contains the numbers you need to write narrative; the `evidence` dict (only in `--full` or via `probe <name>` drill) has structured rows for chart/table rendering.

## Two-layer workflow (RECOMMENDED)

**Step A — situate with `playbook` (compact, default).** One call returns top-8 findings per probe with descriptions. ~25 KB JSON. Read all 11 probe outputs. The descriptions contain the narrative numbers needed for most reports.

**Step B — drill via `probe <name>` only when you need structured data.** If you want to render a chart/table for a specific probe's findings, call `cube probe <name>` to get the full output (all findings + evidence). For 70% of reports the compact playbook alone is enough.

## Synthesis rules

- **Rank by magnitude.** Each probe pre-sorts; you pick the top relevant findings per the user's question.
- **Cross-probe storylines.** A finding on one probe is a question; pairing with a second probe is an answer. (Provider X up in `share_drift` → which Wijk per `interactions`? Premium per `relative_pricing`?)
- **Always pair categorie with age.** If you mention a Categorie value by name, also cite the dominant `Leeftijd_band` and avg age from `age_alignment` in the same paragraph.
- **Concentration probe** must surface TWO bullets: (a) single-service share, (b) heavy-stacker tail (3+).
- **Anomaly probe negative-batch** must report THREE numbers: row count, time cluster, AND min/max range.
- **Q4 / correction-batch caveat**: when `anomaly_scan` flags a negative-bedrag batch, any `unit_price step_change` whose interval crosses that batch year is an artefact — label it as such or omit.
- **Generalist rule**: if a value falls in `share_drift` AND has zero positive cells in `interactions` AND `relative_pricing` shows it within ±5% of median → label it explicitly as "generalist losing across the board".

## Output

The CLI emits compact JSON to stdout. Read the `probes` map; iterate findings; cite from `description`.

For agent reports: typed findings become sections in the AnalysisResult JSON; the `evidence` dict (when drilled) becomes `tool_output.data` rows for tables/charts. Pair each section's `tool_output.query` with the exact `cube` command that produced the rows.

## Examples

```bash
# Budget cube — Iv3 spend by taakveld / year for any groeikern.
# Point it at the live Iv3 CSV in your working directory:
cube playbook --csv ./groeikernen_iv3/capelle-aan-den-ijssel/iv3/2026/data.csv

# Citizen complaints over time × wijk × category
cube playbook --csv ./meldingen.csv

# Any other tabular extract the user hands you
cube playbook --csv ./some_export.csv

# Inspect what schema inference picks if the report looks off
cube infer-schema --csv ./some_export.csv
```

# CBS StatLine — Agent Tool Guide

CBS (Centraal Bureau voor de Statistiek) is Statistics Netherlands. It publishes ~9000 statistical
tables covering the full Dutch population: demographics, labor, housing, income, crime, education,
health, benefits, energy, and more — from national level down to individual neighborhoods (buurten).

This tool gives you direct CLI access to all of it.

---

## Commands

```
cbs search <query> [--geo buurt|wijk|municipality] [--after YEAR] [--limit N] [--expand] [--no-rag]
cbs describe <table_id>
cbs get <table_id> [--geo CITIES] [--level buurt|wijk|municipality] [--period YEAR]
                   [--dim "Name=Label"] [--series] [--stats] [--preview]
                   [--format json|csv|md] [--out FILE]
cbs series <table_id> [--diff]
cbs availability <concept> [--level buurt|wijk|municipality]
cbs municipalities [SEARCH]  [--format json|md]
cbs catalog build | update | stats
cbs rag enrich | index | stats   # admin: builds/updates semantic search index
```

---

## Workflow

**Standard pattern:**
1. `cbs search "<topic>"` — find candidate tables (grouped by series by default)
2. `cbs describe <id>` — see dimensions with labels, geo levels, **metrics with exact column names**, series membership
3. `cbs get <id> --geo "<city>" --level <level>` — fetch data; use column names from step 2
4. Every response includes a `next` array of ready-to-run follow-up commands

**Shortcut for known concepts:**
```bash
cbs availability unemployment --level buurt   # tells you exactly which table, column, and years
```

**Time series pattern (multi-year question):**
1. `cbs search "<topic>" --level buurt`
2. `cbs series <id>` — see all annual editions and year range
3. `cbs series <id> --diff` — check if column names changed between first and last edition
4. `cbs get <id> --geo "<cities>" --level buurt --series` — fetches all editions, merges with `_year` column

**Multi-city pattern:**
```bash
cbs get 86258NED --geo "Capelle aan den IJssel,Zoetermeer,Rotterdam,Den Haag" --level wijk
```
Names are resolved automatically. Output includes `_gm_code` per row.

---

## CBS Data Model

Every table has:
- **Dimensions** — what you filter on (Geslacht, Leeftijd, Perioden, RegioS, WijkenEnBuurten, etc.)
- **Metrics / Topics** — the actual numbers (counts, percentages, averages)
- **Perioden** — time dimension; annual = `2024JJ00`, quarterly = `2024KW01`

**`cbs describe` resolves all dimension codes to plain labels so you never need to know codes.**

---

## Geo Hierarchy

```
national → landsdeel → province (PV) → COROP → municipality (GM) → wijk (WK) → buurt (BU)
```

Not every table goes to wijk/buurt — always run `cbs describe` to check `geo_levels` first.

**Two geo dimension types:**
- `WijkenEnBuurten` — neighborhood tables, goes to buurt. Filter with `--level buurt/wijk/municipality`.
- `RegioS` — regional tables, usually stops at municipality or province.

**Capelle aan den IJssel — GM0502**
- 9 wijken: Capelle West en 's Gravenland, Middelwatering West/Oost, Oostgaarde Noord/Zuid,
  Schenkel, Schollevaar Noord/Zuid, Rivium
- ~88 buurten: IJsseldijk, Redebuurt, Fascinatio-west/oost, Rivium Promenade, etc.
- Run `cbs describe <id>` → geo dimension section to get exact wijk/buurt names for a table

**Other municipalities (use name or GM code):**
- Zoetermeer: GM0637
- Rotterdam: GM0599
- Amsterdam: GM0363
- Den Haag: GM0518
- Utrecht: GM0344
- Eindhoven: GM0772
- Most Dutch municipality names are resolved automatically by name

---

## Concept Guide

What CBS calls common concepts, and which series to use:

### Labor / Employment
| Concept | CBS term | Best series | Geo depth | Years |
|---|---|---|---|---|
| Employment participation | Nettoarbeidsparticipatie | Arbeidsdeelname; wijken en buurten | buurt | 2019–2024 |
| Employment participation | Nettoarbeidsparticipatie | Arbeidsdeelname; gemeenten | municipality | 2014–2023 |
| Unemployment rate | Werkloosheidspercentage | Arbeidsdeelname; kerncijfers | national | 2013–2025 |
| Unemployment rate | Werkloosheidspercentage | Arbeidsdeelname; provincie | province | 2013–2025 |
| WW benefit recipients | Werkloosheidsuitkering | Personen met een uitkering; wijken en buurten | buurt | 2013–2025 |
| Bijstand (welfare) | Bijstandsuitkering | Personen met een uitkering; wijken en buurten | buurt | 2013–2025 |
| Labor force | Beroepsbevolking | Arbeidsdeelname; kerncijfers | national | 2013–2025 |

> **Note:** True unemployment *rate* (%) does not exist at wijk/buurt level. The finest available
> proxy is **WW uitkering %** (unemployment benefit recipients as % of residents 15+) at buurt level
> from 2013. For pre-2013, use Kerncijfers wijken en buurten (NietActieven as proxy, 1995–2025).

### Income
| Concept | CBS term | Best series | Geo depth | Years |
|---|---|---|---|---|
| Average income per resident | GemiddeldInkomenPerInwoner | Kerncijfers wijken en buurten | buurt | 1995–2025 |
| Average income per recipient | GemiddeldInkomenPerInkomensontvanger | Kerncijfers wijken en buurten | buurt | 1995–2025 |
| Low income households | 40% personen met laagste inkomen | Kerncijfers wijken en buurten | buurt | 2014–2025 |
| Poverty | PersonenInArmoede | Kerncijfers wijken en buurten | buurt | 2018–2025 |

### Housing
| Concept | CBS term | Best series | Geo depth | Years |
|---|---|---|---|---|
| Housing stock | Woningvoorraad | Kerncijfers wijken en buurten | buurt | 1995–2025 |
| WOZ value (avg) | GemiddeldeWOZWaardeVanWoningen | Kerncijfers wijken en buurten | buurt | 2014–2025 |
| Owner-occupied | Koopwoningen % | Kerncijfers wijken en buurten | buurt | 2014–2025 |
| Social housing | InBezitWoningcorporatie | Kerncijfers wijken en buurten | buurt | 2014–2025 |

### Demographics / Population
| Concept | CBS term | Best series | Geo depth | Years |
|---|---|---|---|---|
| Population | AantalInwoners | Kerncijfers wijken en buurten | buurt | 1995–2025 |
| Age groups | 0-15j, 15-25j, 25-45j, 45-65j, 65+ | Kerncijfers wijken en buurten | buurt | 1995–2025 |
| Non-Dutch origin | BuitenEuropa / EuropaExclNL | Kerncijfers wijken en buurten | buurt | 2014–2025 |
| Household composition | HuishoudensMetKinderen etc. | Kerncijfers wijken en buurten | buurt | 2014–2025 |

### Crime
| Concept | CBS term | Best series | Geo depth | Years |
|---|---|---|---|---|
| Registered crimes | Geregistreerde misdrijven | Geregistreerde misdrijven; wijken en buurten | buurt | 2016–2018 |

### Education
| Concept | CBS term | Best series | Geo depth | Years |
|---|---|---|---|---|
| Education level | BasisonderwijsVmboMbo1, HavoVwoMbo24, HboWo | Kerncijfers wijken en buurten | buurt | 2018–2025 |
| Pupils primary school | LeerlingenPo | Kerncijfers wijken en buurten | buurt | 2018–2025 |

### Benefits / Social
| Concept | CBS term | Best series | Geo depth | Years |
|---|---|---|---|---|
| All benefit types | WW, Bijstand, AO, AOW | Personen met een uitkering; wijken en buurten | buurt | 2013–2025 |
| Youth care | JongerenMetJeugdzorgInNatura | Kerncijfers wijken en buurten | buurt | 2018–2025 |

---

## Key Series Reference

The most important series for neighborhood-level analysis:

| Series base title | Years | Geo | What it contains |
|---|---|---|---|
| Kerncijfers wijken en buurten | 1995–2025 | buurt | Everything: population, income, housing, labor, education, benefits — 100+ metrics |
| Arbeidsdeelname; wijken en buurten | 2019–2024 | buurt | Employment participation by gender and age |
| Personen met een uitkering; wijken en buurten | 2013–2025 | buurt | WW, bijstand, AO, AOW benefit counts and % |
| Arbeidsdeelname; gemeenten | 2014–2023 | municipality | Employment by gender, age, education, origin |
| Arbeidsdeelname; kerncijfers | 2013–2025 | national/province | Unemployment rate, labor force |
| Geregistreerde misdrijven; wijken en buurten | 2016–2018 | buurt | Crime by type |
| Bevolking 15-75 jaar; opleidingsniveau, wijken en buurten | 2013–2023 | buurt | Education level of working-age population |

> **Kerncijfers wijken en buurten is your starting point for any neighborhood question.**
> It goes back to 1995 and covers almost every topic. When you need deeper breakdowns
> (e.g. employment by gender or age), switch to the topic-specific series.

---

## Limitations and Gotchas

**Privacy suppression:** Small buurten suppress values that could identify individuals.
These appear as `null` in output. Affects: income, crime, benefit percentages in small neighborhoods.
Counts below ~5 are suppressed. This is normal — mention it when reporting results.

**Schema changes across years:** Kerncijfers 1999 has 36 columns; 2024 has 121 columns.
CBS appends running ordinal suffixes to all column names (`NettoArbeidsparticipatie_3` in 2013,
`NettoArbeidsparticipatie_7` in 2024 — same concept). The tool automatically strips these
ordinals when using `--series`, so columns align correctly across editions.
True concept-level additions/removals (new metrics added, old ones dropped) still produce nulls
for years where the column didn't exist — this is expected and informative.
Use `cbs series <id> --diff` to see `reconciled_added`/`reconciled_removed` for the true changes.

**Buurt boundaries change:** Municipalities redraw neighborhoods. A buurt from 2005 may not
exist or may have been split in 2020. Long time series at buurt level require care.

**"Unemployment rate" does not exist at buurt level.** Use:
- WW uitkering % (from Personen met een uitkering series, 2013–2025)
- Netto arbeidsparticipatie (from Arbeidsdeelname wijken en buurten, 2019–2024)
- NietActieven (from Kerncijfers, as proxy, 1995–2013)

**Large tables:** Some tables have 40M+ rows. Always use `--geo` to filter before fetching.
`cbs describe` shows `records` — if > 500k, always filter.

**`--dim` + `--geo` together:** The tool applies geo filtering both at OData level and in-memory as a safety net. If CBS ignores the OData geo filter (which can happen silently for some tables), the in-memory filter ensures you only get rows for the requested municipality. You should never need to manually filter by geo in pandas.

**Perioden format:**
- Annual: `2024JJ00` (pass `--period 2024`, tool converts automatically)
- Quarterly: `2024KW01` (Q1), `2024KW02` (Q2), etc.
- Monthly: `202401` (January 2024)

**Quarterly tables and deduplication:**
`cbs get` always returns a `frequency` field: `"Perjaar"` = annual, `"Perkwartaal"` = quarterly.
Quarterly tables also return a `frequency_note` warning in the JSON when detected.

For quarterly tables (e.g. "Personen met een uitkering"), each edition contains multiple quarters —
giving you 4 rows per buurt per year. The quarterly values genuinely differ (march ≠ december),
so the tool does not auto-deduplicate. Before year-over-year trend analysis, pick one snapshot:

```python
# Keep only december snapshot (end-of-year, most complete)
df = df[df["Perioden"].str.contains("december")]
```

When `frequency_note` is present in the response, apply this filter before aggregating.

---

## Example Workflows

**"WW uitkering per buurt in Capelle, 2013–2025"**
```bash
cbs describe 86003NED                        # frequency: Perkwartaal → quarterly table
cbs series 86003NED                          # 13 editions 2013-2025
cbs get 86003NED --geo "Capelle aan den IJssel" --level buurt --series --format csv --out ww_capelle.csv
# Post-process: filter to december snapshot before year-over-year analysis
# df = df[df["Perioden"].str.contains("december")]
```

**"Compare income across buurten in Capelle vs Zoetermeer"**
```bash
cbs search "inkomen wijken buurten" --level buurt
cbs describe 85984NED                        # Kerncijfers 2024
cbs get 85984NED --geo "Capelle aan den IJssel,Zoetermeer" --level buurt --format csv --out income_compare.csv
```

**"Employment participation by gender in Capelle wijken"**
```bash
cbs describe 86258NED                        # shows Geslacht dimension: Mannen, Vrouwen, Totaal
cbs get 86258NED --geo "Capelle aan den IJssel" --level wijk --dim "Geslacht=Mannen"
cbs get 86258NED --geo "Capelle aan den IJssel" --level wijk --dim "Geslacht=Vrouwen"
```

**"Population trend per wijk in Capelle 1995–2025"**
```bash
cbs series 86165NED                          # Kerncijfers series: 18 editions 1995-2025
cbs get 86165NED --geo "Capelle aan den IJssel" --level wijk --series --format csv --out population_trend.csv
```

**"What buurt-level data exists for unemployment?"**
```bash
cbs search "werkloosheid uitkering buurt" --level buurt
# → finds Personen met een uitkering (WW proxy, 2013-2025)
# Note: true unemployment rate only exists at municipality level and above
cbs search "arbeidsdeelname buurt" --level buurt
# → finds Arbeidsdeelname wijken en buurten (employment participation, 2019-2024)
```

---

## Output Format

All commands output JSON by default (agent-friendly). Every response includes:
- `data` or `results` — the actual content
- `next` — array of ready-to-run follow-up commands

Use `--format md` for human-readable markdown tables.
Use `--format csv --out <file>` to save for further analysis.
Use `--stats` to get per-column min/max/mean/median/null_count without fetching all rows.
Use `--preview` to return only the first 5 rows (fast sanity check).

**Every `cbs get` row includes these context columns:**
- `_year` — year (from Perioden if present, else from table metadata)
- `_gm_code` — GM code of the municipality (when `--geo` is used)
- `_geo_name` — human-readable municipality name
- `_table_id` — source table ID (only when `--series` is used)

**JSON output also includes:**
- `null_counts` — dict of columns that have any null values, with count. Use this to quickly assess data completeness before analysis.
- `search_mode` — `"rag"` when ChromaDB index was used, `"keyword"` when falling back to catalog title search

**`cbs get --stats` output also includes:**
- `null_only_columns` — list of columns that are entirely null (all values suppressed). Skip these when selecting metrics.

**`cbs get --series` JSON output also includes:**
- `reconciliation_warnings` — list of warnings for lossy concept renames applied during the merge.
  Always check this field. Example warning: `"Column 'Allochtonen' (pre-2014, Western + non-Western combined) was mapped to 'NietWestersAllochtonen' (post-2014). This is lossy..."`.
  If present, the column values before and after the rename transition year are not directly comparable.

**`cbs describe` metrics format:**
Each metric is `{"label": "Werkloosheidsuitkering (% van het aantal inwoners vanaf 15 jaar)", "column": "Werkloosheidsuitkering_9"}`.
The `column` field is the **exact column name** you will find in `cbs get` output. Always use `describe` before `get` to know which column to analyze.

**`cbs describe` also includes:**
- `editions_nav` — `{"prev": {"id": "...", "period": "..."}, "next": {...}}` for navigating to adjacent editions in the series
- `note` — includes specific null-suppression rate estimates: ~20–40% at buurt level, ~5–15% at wijk level

**`cbs series <id> --diff` output:**
```json
{
  "schema_diff": {
    "from": {"id": "...", "period": "..."},
    "to":   {"id": "...", "period": "..."},
    "raw_added":               ["NewColumn_7", ...],
    "raw_removed":             ["OldColumn_3", ...],
    "raw_stable_count":        2,
    "reconciled_added":        ["TrueNewConcept"],
    "reconciled_removed":      ["DroppedConcept"],
    "reconciled_stable_count": 89,
    "note": "Raw: 47 added, 51 removed (mostly ordinal drift). After reconciliation: 1 true concept addition, 2 true removals, 89 stable columns."
  }
}
```
`raw_*` shows the full column mismatch with CBS ordinal suffixes (e.g. `_3` vs `_7`).
`reconciled_*` shows what remains after the tool auto-strips those suffixes — the true concept-level changes.
Run this before `--series` to understand the actual semantic changes across editions.

**`cbs municipalities [SEARCH]`:**
Lists all Dutch municipalities with GM codes. Requires `cbs catalog build` to have been run first.
Supports optional name filter: `cbs municipalities capelle` → `[{"code": "GM0502", "name": "Capelle aan den IJssel"}]`

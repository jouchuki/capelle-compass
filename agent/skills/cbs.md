---
description: Query CBS StatLine — ~9000 Dutch statistical tables from national down to neighbourhood level
argument-hint: <search query> | describe <table_id> | get <table_id> [--geo "Capelle aan den IJssel"] | availability <concept>
---

Query CBS (Statistics Netherlands) data for any Dutch municipality the user names.

Request: $ARGUMENTS

## CLI

`cbs` is on $PATH in the configured Compass agent tools environment.

```bash
cbs search "<query>" [--geo buurt|wijk|municipality] [--after YEAR] [--limit N]
cbs describe <table_id>
cbs get <table_id> --geo "Capelle aan den IJssel" [--level buurt|wijk|municipality] [--period YEAR] [--series]
cbs availability <concept>
cbs series <table_id> [--diff]
```

## Standard workflow

1. `cbs search "<topic>"` — find tables
2. `cbs describe <id>` — see exact column names and geo levels
3. `cbs get <id> --geo "Capelle aan den IJssel" --level wijk` — fetch data
4. Every response includes a `next` array of ready-to-run follow-up commands

## Discover, don't anchor on table IDs

Run `cbs search <onderwerp>` per indicator to find the right tables for the question at hand.
**Any table IDs named anywhere in this skill are examples and starting points, not an exhaustive
list.** A comparison that cites only 2–3 tables has likely under-explored CBS — the catalogue has
~9,000 tables. Always search broadly; pick the tables that actually fit the question.

**Two catalogues, one tool.** `cbs search`/`describe`/`get` span BOTH **CBS StatLine**
(opendata.cbs.nl) AND **CBS DataDerden** (dataderden.cbs.nl) — roughly half the catalogue (~3k
tables) is DataDerden: third-party / regional / sector datasets (zorg, jeugd, RIVM, politie,
provincie, etc.) published on CBS infrastructure. Routing is **automatic per table** — a DataDerden
hit in your search results is first-class and `cbs get <id>` reaches it transparently (no flag
needed). Treat DataDerden tables as equal to StatLine; do not skip them.

## Key concepts and which series to use

**Crime / Safety**
- `cbs availability crime` — registered crimes at buurt level (2016–2018)
- Table: Geregistreerde misdrijven; wijken en buurten
- **Correctness caveat — post-2018 crime data:** tables `47004NED` and `47005NED` freeze at 2018;
  do NOT use them for recent figures. For 2019 and later use `47018NED` or `47022NED` (check
  availability with `cbs describe`). Always verify the period coverage with `cbs describe <id>`
  before citing a table as "current".

**Income**
- `cbs availability income` — GemiddeldInkomenPerInwoner, buurt level 1995–2025
- Table: Kerncijfers wijken en buurten

**Unemployment / Benefits**
- `cbs availability unemployment` — WW uitkering % at buurt (2013–2025)
- Note: true unemployment rate only exists at municipality level, not buurt

**Housing**
- `cbs availability housing` — WOZ, woningvoorraad, koopwoningen at buurt 2014–2025

**Demographics**
- `cbs availability demographics` — AantalInwoners, age groups, origin at buurt 1995–2025

**Proxy rule — no wijk-level gender split in CBS:**
CBS does not publish a wijk-level gender breakdown of household types.
When the question involves "alleenstaande moeders" (single mothers) or any other single-gender
household slice, fall back to `eenouderhuishoudens` (single-parent households, table `85252NED`)
as the closest available proxy. **Always state the proxy explicitly in the narrative** — e.g.
*"CBS heeft geen wijkcijfers voor alleenstaande moeders; als proxy zijn eenouderhuishoudens
gebruikt (tabel 85252NED)."* Apply the same pattern for any other single-gender slice: name the
proxy, cite the table, and flag the limitation.

**Best starting point for any neighbourhood question:**
```bash
cbs search "kerncijfers wijken buurten" --level buurt
```
Kerncijfers wijken en buurten (1995–2025) covers almost everything at buurt level.

## Geo levels for Capelle

- Municipality: `--level municipality --geo "Capelle aan den IJssel"`
- Wijken (9): `--level wijk --geo "Capelle aan den IJssel"`
- Buurten (~88): `--level buurt --geo "Capelle aan den IJssel"`

Capelle GM code: GM0502

## Output size — ALWAYS CHECK FIRST

Every CBS table has a dimension cross-product (Dim1 × Dim2 × ... × Perioden).
Some are small, some are enormous. You cannot tell from the table name alone.
Many tables return 2–5 MB of JSON for ONE municipality — enough to crash
your context window and abort the analysis with no final report produced.

**Measured examples (Capelle, `--level municipality`, no `--series`):**

| Table | Raw `cbs get` | `--stats` | `--preview` |
|---|---|---|---|
| 20248NED (jeugdzorg, 2015–2020) | **2.57 MB / 75k lines** | 420 B / 19 lines | 2.9 KB |
| 20313NED (jeugdzorg, 2021–) | **1.90 MB / 55k lines** | 420 B | 2.9 KB |

Calling raw `cbs get` on either of those wipes out the context. `--stats`
gives the same analytical answer in 420 bytes.

### The mandatory first-pass sequence for EVERY `cbs get`

1. `cbs describe <id>` — inspect the dim cardinality. Multiply all dim counts
   × (period count) — if the product is >500, the raw `get` will be large.
2. **Start with `--stats`**: if the question is "how does X trend / compare /
   average?", the 420-byte stats response IS the answer. Do NOT fetch raw
   rows just to compute an average yourself.
3. If `--stats` isn't enough, use `--preview` (first 5 rows) to see the
   column shape, then narrow via `--dim` or `--period`.
4. Only fall to raw rows when you genuinely need row-level detail — and
   pair with `--out ./cbs-<id>.csv --format csv`.

### Size-control flags — PRIORITY ORDER (top = try first)

| Flag | Output size | When to use |
|---|---|---|
| `--stats` | ~0.4 KB | Any trend/aggregate/comparison question. **Default for 80% of queries.** |
| `--preview` | ~3 KB | "What columns does this have?" or "is there data at all?" |
| `--period 2024` | varies | Single year of a multi-year table |
| `--dim "DimName=Label"` | 10–100× reduction | Narrow a dim to a specific total/category (labels from `cbs describe`) |
| `--out ./X.csv --format csv` | 0 B stdout | Raw rows needed; write to CWD, read back with pandas |
| raw `cbs get <id>` | UNBOUNDED | **Only use when you know the table is small** (dim product < 500 × geo count × period count) |

### Rules of thumb

- **Trend / aggregate / comparison question** → `--stats` first. Nearly always enough.
- **"Which wijk is highest" / "how does it break down"** → `--dim "DimName=Total"` for each non-focus dim, then raw fetch (small result)
- **"List every buurt where X"** → `--level buurt --period YEAR` (single year ≈ 88 rows, fits in context) + `--dim` filters on other dims
- **Full multi-year dataset** → `--series --out ./cbs-<id>.csv --format csv` + pandas — NEVER to stdout
- `--dim` labels come from `cbs describe`. Prefer labels marked `(total)` if you just want the overall — those collapse a dim to its summary

### Pandas path (when you DO need raw rows)

Use `python3` from the agent tools environment, which includes pandas. `/usr/bin/python3`
does NOT have pandas and will fail with ModuleNotFoundError.

```bash
cbs get <id> --geo 'Capelle aan den IJssel' --level wijk \
    --series --format csv --out ./cbs-<id>.csv

python3 - <<'PY'
import pandas as pd
df = pd.read_csv('./cbs-<id>.csv')
print(df.groupby('_year')['SomeMetric'].sum())
PY
```

The model is BETTER at aggregation via pandas than at eyeballing 10K rows —
fewer arithmetic errors, provable numbers.

## Important gotchas

- **Perioden format**: annual = `2024JJ00` (use `--period 2024`, tool converts automatically)
- **Quarterly tables**: filter to december snapshot before year-over-year comparison
- **Privacy suppression**: small buurten have nulls for sensitive data — normal
- **Schema drift**: CBS renames columns across years; use `--series` for multi-year, tool auto-reconciles across the rename — but ALWAYS pair `--series` with `--out` (see Output size above)

## For the test query "criminal activity in Capelle"

```bash
cbs search "misdrijven criminaliteit buurt" --level buurt
cbs availability crime --level buurt
# Then fetch with:
cbs get <crime_table_id> --geo "Capelle aan den IJssel" --level buurt --format json
```

Note: CBS crime data only goes to 2018. For more recent data, use beleid (policy) tools or external sources.

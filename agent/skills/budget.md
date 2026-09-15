---
name: budget
description: Read & total the Iv3 budget data for the municipalities in the corpus, 2020-2026 — no CLI, you read the CSVs in your working directory directly
argument-hint: <gemeente / taakveld / year question about the begroting>
---

Analyse municipal budget (Iv3) for the municipalities in the corpus straight
from the data in your working directory. **There is no budget CLI** — you read
and aggregate the CSVs yourself, applying the rules below. Compute every figure
from the data and present it as your own finding; never cite "the instructions"
or this skill as a source.

Request: $ARGUMENTS

## Where the data is

`./groeikernen_iv3/<gemeente>/iv3/<jaar>/data.csv` — one file per town per
year, 2020–2026.

Gemeente folders: `capelle-aan-den-ijssel`, `almere`, `zoetermeer`,
`nieuwegein`, `purmerend`, `lelystad`, `houten`.

Columns (CSV, quoted):
- `Verslagsoort` — the **budget phase**. Codes end in `X000`=**Begroting**,
  `X001`–`X004`=kwartaalrapportages, `X005`=**Jaarrekening** (realisatie). One
  file can hold several phases (older years have all six; 2026 has only the
  begroting).
- `Categorie` — `L*` = a **Lasten** (expense) row, `B*` = a **Baten** (revenue) row.
- `Post` — the **taakveld** code (e.g. `0.1`, `6.1`, `7.2`). `A*`/`P*` posts are
  balance/aggregate entries, NOT real taakvelden.
- `Baten`, `Lasten` — amounts in **× €1.000**. The non-applicable side is `\N`.

Code → label tables (shared across towns):
- `./groeikernen_iv3/_shared/iv3/<jaar>/verslagsoort.csv` — phase code → name.
- `./groeikernen_iv3/_shared/iv3/<jaar>/post.csv` — taakveld code → name.
- `./groeikernen_iv3/_shared/iv3/<jaar>/categorie.csv` — categorie code → name.

## How to total it CORRECTLY — read this before you sum anything

1. **Pick ONE phase** via `Verslagsoort`: for a *begroting* figure keep only
   the Begroting rows (code ending `X000`); for *realisatie*/actuals keep
   Jaarrekening (`X005`). **NEVER sum across phases** — stacking begroting +
   kwartalen + jaarrekening inflates the total ~6× (this is the #1 mistake on
   older years, which contain all six phases).
2. **Filter to real taakvelden**: keep rows whose `Post` starts with a DIGIT.
   SKIP `A*`/`P*` posts (balance/aggregate, not spending).
3. **Sum ONE side**: `Lasten` for spending, `Baten` for income. **NEVER add
   Lasten + Baten** — that double-counts the begroting.
4. **Reserves**: taakveld `0.10` (mutaties reserves) is usually excluded from
   the headline begroting — state whether you include it.

### Structural self-checks (reason about SHAPE — there are no expected values)
- A begroting balances: taakveld Lasten ≈ Baten. Wildly unequal → something's off.
- Consecutive years are the same order of magnitude. A sudden several-fold jump
  between years means you summed multiple Verslagsoort phases — recompute with one.
- A "total" that is ~double one side means you summed Lasten + Baten.

Derive every figure from the data; never quote a number as coming from this skill.

## Reading recipe (robust — stdlib `csv`, no pandas needed)

Write a tiny script to your working directory with the `Write` tool, then run
it (avoid Bash heredocs — they're unreliable here):

```python
# ./budget_sum.py  —  usage: python3 ./budget_sum.py <data.csv> [X000|X005]
import csv, sys
NULL = "\\N"
def num(v): return 0.0 if v in (None, "", NULL) else float(v)
phase = sys.argv[2] if len(sys.argv) > 2 else "X000"      # begroting by default
rows = list(csv.DictReader(open(sys.argv[1])))
sel = [r for r in rows
       if r["Verslagsoort"].endswith(phase) and r["Post"][:1].isdigit()]  # phase + taakveld, skip A*/P*
lasten = sum(num(r["Lasten"]) for r in sel)               # spending side ONLY
baten  = sum(num(r["Baten"])  for r in sel)
print(f"{phase} taakveld Lasten: {lasten:,.0f} k = EUR {lasten/1000:,.1f} M")
print(f"{phase} taakveld Baten : {baten:,.0f} k = EUR {baten/1000:,.1f} M")
```
```bash
python3 ./budget_sum.py ./groeikernen_iv3/capelle-aan-den-ijssel/iv3/2026/data.csv
# sanity-check the SHAPE: Lasten ≈ Baten (a begroting balances), not double
```

- **Per-taakveld breakdown**: group the selected L-rows by `Post`, join to
  `post.csv` for labels.
- **Cross-town comparison**: same phase, same year, per gemeente folder;
  tabulate side by side. Pair Iv3 spend with CBS context where it helps.
- **Trend**: loop the years for one town, ALWAYS with the same phase filter.

## Rules
- Amounts are in thousands of euros (× €1.000) — convert for the narrative.
- Cite budget figures with `source: "budget"`, `doc_type: "Iv3-taakveld"`,
  `document: "<gemeente> Iv3 <jaar> (begroting)"`, `year: <jaar>`. No `source_url`.
- `Lasten` can be negative (corrections) — keep the sign.
- Never recommend allocations; describe what the figures imply for the reader.

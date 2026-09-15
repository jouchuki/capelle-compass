---
name: buitenbeter
description: Query BuitenBeter citizen public-space complaint reports (meldingen) for Capelle aan den IJssel
argument-hint: <category, status filter, or question about public-space complaints>
---

# BuitenBeter — citizen public-space complaints (meldingen)

BuitenBeter is the national citizen-reporting platform for public-space issues.
Capelle residents submit meldingen (complaints/reports) in categories such as
**afval** (waste), **verlichting** (street lighting), **verharding** (pavement),
**groen** (greenery), and **overlast** (nuisance).

**Use sparingly — never as a primary source.** Meldingen data is noisy: volume
is driven by reporting behaviour as much as actual conditions. Use it to corroborate
a finding from CBS or the bewonersenquete, or to spot a recent spike; never build a
headline conclusion on it alone.

## CLI

`capelle-buitenbeter` is on $PATH in the agent workspace.

```bash
capelle-buitenbeter query \
    [--category <categorie>]   \  # e.g. "afval", "verlichting", "verharding"
    [--status <status>]        \  # e.g. "open", "closed"
    [--open-only]              \  # shorthand for unresolved meldingen
    [--limit <N>]              \  # cap number of records returned
    [--output-json]               # structured JSON (default mode — always use this)
```

Always include `--output-json`. Plain-text output is harder to parse and wastes tokens.

## Output-size discipline — enforced, no exceptions

Raw meldingen are ~500 bytes each after JSON serialisation.

| Call pattern | Approx. output |
|---|---|
| `--output-json` with no `--limit` | ~100 KB (≈200 records) |
| `--limit 200 --output-json` | ~82 KB |
| `--limit 20 --output-json` | ~8 KB — fits comfortably |

Both of the first two patterns can overflow context, especially when combined with other
large-output calls in the same turn.

**Rules:**

1. **Always pass `--limit <=20`** unless the query is already narrowed by `--category`,
   `--status`, or `--open-only`. A narrowed query (single category + open-only) is
   typically 10–40 records and safe at `--limit 50`.

2. **For trend or volume questions, prefer two narrow calls over one broad one:**
   ```bash
   # Good — two focused slices
   capelle-buitenbeter query --category "afval" --open-only --output-json --limit 20
   capelle-buitenbeter query --category "verlichting" --open-only --output-json --limit 20

   # Bad — one broad dump
   capelle-buitenbeter query --output-json --limit 200
   ```

3. **Never combine `--limit 200` (or no `--limit`) with another large-output call** —
   such as `cbs get --series` or a `pdftotext ... -` stdout pipeline — in the same turn.

## Typical use patterns

```bash
# Recent open afval complaints (quick scan)
capelle-buitenbeter query --category "afval" --open-only --output-json --limit 20

# Check resolution rate for verlichting
capelle-buitenbeter query --category "verlichting" --output-json --limit 20

# Overview of open issues across all categories
capelle-buitenbeter query --open-only --output-json --limit 20
```

## Interpreting results

- **Category distribution**: confirms which types of issues dominate; compare with
  enquete themes (e.g. high verlichting meldingen aligning with low veiligheid scores).
- **Open vs resolved ratio**: a persistently high open ratio in one category can signal
  a capacity or prioritisation issue in the relevant service team.
- **Volume over time**: spikes in a category may map to a seasonal pattern (afval around
  holidays) or a genuine deterioration — triangulate with CBS leefbaarheid indicators
  before drawing conclusions.
- **Geographical signal**: individual meldingen may carry a buurt or address field; use
  this to spot concentration, but note that reporting density (not just problem density)
  varies by neighbourhood.

## Cross-reference with other sources

BuitenBeter findings gain weight when they align with:
- **Bewonersenquete**: low rapportcijfer for fysieke kwaliteit or veiligheid in the same wijk
- **CBS**: leefbaarheid or overlast indicators at buurt level
- **Beleid**: programme commitments on openbare ruimte, groen, or veiligheid

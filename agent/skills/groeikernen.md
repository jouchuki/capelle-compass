---
name: groeikernen
description: Full-text search across municipal regelgeving — CVDR consolidated bylaws + Gemeenteblad version history
argument-hint: <search query> [--gemeente slug] [--source cvdr|bekendmakingen] [--rubriek type]
---

Full-text search over the municipal regelgeving corpus.

Query: $ARGUMENTS

## What's indexed

| Source | Count | What it contains |
|---|---:|---|
| **cvdr** | run `groeikernen coverage` | Consolidated municipal bylaws currently in force (one file per regeling, latest version) — clean Markdown |
| **bekendmakingen** | run `groeikernen coverage` | Gemeenteblad publications: each new/amended verordening, beleidsregel, ander besluit van algemene strekking, delegatie/mandaatbesluit — raw HTML, stripped on read |

Coverage depends on the local corpus. Run `groeikernen coverage` for the current municipality and source counts.

## CLI

```bash
groeikernen search "<query>" [--gemeente SLUG]... [--source SRC]... [--rubriek RUB]...
                             [--sort score|gemeente|date]
                             [--page N] [--page-size N] [--context-words N] [--pretty]
groeikernen coverage      # how many docs per gemeente/source
groeikernen show <doc_id> # file path(s) for one document
groeikernen index         # one-time text cache build (~30 s, idempotent)
```

**Results are paginated** (default `--page-size 50`). Every response carries
`total`, `page`, `has_more`, and a ready-to-run next-page command in `next`.

**First-time setup:** run `groeikernen index` once to populate the text cache. After that, queries return in 100–700 ms.

## Output shape

> ⚠️ **The block below is an illustrative example only.** The exact values
> (`doc_id`, `title`, `date`, `path`, `score`) shown here are placeholders.
> **Never re-use any path or doc_id from this skill markdown — always
> run `groeikernen search` yourself to get the real values for the user's
> question, then use those.**

Every `search` call returns JSON shaped like:

```json
{
  "query": "<EXAMPLE — your query>",
  "total": 242,
  "page": 1,
  "page_size": 50,
  "returned": 50,
  "has_more": true,
  "filters": { "gemeente": null, "source": null, "rubriek": null },
  "context_words": 100,
  "results": [
    {
      "gemeente": "<EXAMPLE — slug, e.g. capelle-aan-den-ijssel>",
      "source": "<EXAMPLE — cvdr | bekendmakingen>",
      "doc_id": "<EXAMPLE — gmb-YYYY-NNNNNN or CVDRNNN_vN>",
      "rubriek": "<EXAMPLE — bekendmakingen-only label>",
      "cvdr_type": "<EXAMPLE — cvdr-only label>",
      "title": "<EXAMPLE — full regeling title>",
      "date": "<EXAMPLE — yyyy-mm-dd>",
      "path": "<EXAMPLE — absolute path; the REAL one comes from the live response>",
      "score": 31,
      "matches": 10,
      "snippet": "<EXAMPLE — ~100 words around the strongest match>"
    }
  ],
  "next": [
    "<EXAMPLE — next-page command with filters preserved>",
    "<EXAMPLE — narrow-by-gemeente suggestion>",
    "<EXAMPLE — groeikernen show <doc_id>>"
  ]
}
```

**To answer a real user question:** run the `groeikernen search` command,
substituting `<query>` with the user's topic and adding `--gemeente <slug>`
if they named a specific city. Then use the *actual* `results[N].path`
from the response with the Read tool to quote exact wording — never use
a path from this skill markdown.

The first entry in `next` is always the next-page command (when `has_more`
is true), with all your filter flags preserved — so paging through results
is one command away.

**To read a hit in full**, open `result.path` with the Read tool — that's the original markdown (CVDR) or raw HTML (bekendmakingen). The paths the search returns are real absolute paths on this host (e.g. `.../data/groeikernen/<gemeente>/<source>/...`); pass them verbatim to Read, do not paraphrase or shorten.

### Required flow when the user asks for specifics (rates, articles, exact text)

Snippets in search results are only ~100 words of context — useful for *picking* the right hit, not for quoting exact wording. The right pattern is **always**:

1. **Run** `groeikernen search …` with appropriate filters. Capture the JSON response.
2. **Inspect** the `results` array. Pick the hit(s) most relevant to the user's question by `title`, `date`, `score`, and `snippet`.
3. **Read** the file at `results[N].path` using the Read tool, where `N` is the index of your chosen hit. The path is dynamic — it comes from this turn's search response, not from this skill markdown. Never paraphrase or shorten the path.
4. **Quote** the relevant article/tarief from the file content back to the user, with a citation that includes the gemeente, doc title, and date.

Skipping step 1 (going straight to Read with a path from memory or from this doc's prose) is wrong — you will miss newer versions and pick stale text. Always re-search.

## Filters

| Flag | Values | Notes |
|---|---|---|
| `--gemeente` | Any municipality slug present in the corpus, e.g. `capelle-aan-den-ijssel`, `almere`, `zoetermeer` | Repeatable; default = all available municipalities |
| `--source` | `cvdr`, `bekendmakingen` | Repeatable; default = both. Use `cvdr` for current consolidated text, `bekendmakingen` for version history |
| `--rubriek` | `algemeen verbindend voorschrift (verordening)`, `beleidsregel`, `ander besluit van algemene strekking`, `delegatie- of mandaatbesluit` (bekendmakingen-only labels); `verordening`, `beleidsregel`, `besluit`, `regeling`, `nadere_regel`, etc. (CVDR-only labels) | Case-insensitive |
| `--page` | int (default 1) | 1-indexed page number |
| `--page-size` | int (default 50) | Results per page |
| `--sort` | `score` \| `gemeente` \| `date` | Result ordering. `score` (default) = hit count desc. `gemeente` = alphabetical by municipality slug (groups results per gemeente — page through Almere, then Capelle, then Houten…). `date` = newest publication first |
| `--context-words` | int (default 100) | Words of context per snippet (split half before / half after match) |

## Standard workflows

### "Does each gemeente levy X-belasting, and at what rate?"
```bash
groeikernen search "toeristenbelasting" --rubriek "algemeen verbindend voorschrift (verordening)"
```
Surfaces the most recent verordening per gemeente; the snippet typically contains the tarief article.

### "How does each gemeente set its PGB-jeugdhulp rate?"
```bash
groeikernen search "pgb informele jeugdhulp"
```
Then narrow with `--gemeente almere` etc. to compare formulas.

### "Did Capelle just publish/amend an X bylaw recently?"
```bash
groeikernen search "X" --gemeente capelle-aan-den-ijssel --source bekendmakingen --limit 20
```
Sort visually by `date` in the response — bekendmakingen are version-stamped.

### "Read the full text of a single regeling"
```bash
groeikernen show CVDR647_v1            # get the path
# then open the .md / .html file directly with Read
```

## Gemeente coverage notes

- Coverage varies by municipality because Gemeenteblad publication history starts later for some municipalities. Use `groeikernen coverage` before making completeness claims.
- CVDR covers currently-in-force consolidated regelingen for municipalities present in the local corpus.

## When to use this vs. other tools

- **`groeikernen`** — full text of bylaws + announcements across 7 cities, version history. Best for "what does the rule say" and "when did it change".
- **`beleid`** — municipal policy documents (begrotingen, jaarstukken, voorjaarsnota, etc.), semantic search. Best for "why is the city doing X" and budget-program context.
- **`cbs`** — Dutch national statistics, all 342 gemeenten. Best for quantitative comparisons (income, crime rates, demographics).

A typical agent flow uses `groeikernen` to find the *legal text*, `beleid` to find the *policy reasoning*, and `cbs` to find the *outcome statistics*.

## Data sources

- CVDR: `lokaleregelgeving.overheid.nl` (consolidated)
- Bekendmakingen: `zoek.officielebekendmakingen.nl` (Gemeenteblad channel, regelgeving rubrieken only)

The corpus lives at `$GROEIKERNEN_DATA` (the search resolves it for you). Refresh scripts: `download_cvdr.py`, `download_bekendmakingen.py`.

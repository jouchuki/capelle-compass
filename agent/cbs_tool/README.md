# cbs_tool — CBS StatLine CLI

A CLI that gives structured access to all ~9000 CBS (Statistics Netherlands) tables, from national level down to individual neighborhoods (buurten). Designed for use by Claude Code agents and humans alike.

## Install

```bash
# From repo root with venv active:
pip install -e cbs_tool/
```

This installs the `cbs` command.

## Commands

```bash
cbs search <query>          [--geo buurt|wijk|municipality] [--after YEAR] [--no-rag]
cbs describe <table_id>
cbs get <table_id>          [--geo CITIES] [--level buurt|wijk|municipality]
                            [--dim "Name=Label"] [--series] [--stats] [--preview]
                            [--format json|csv|md] [--out FILE]
cbs series <table_id>       [--diff]
cbs availability <concept>  [--level ...]
cbs municipalities          [SEARCH]
cbs catalog build | update | stats
cbs rag enrich | index | stats
```

## Quick start

```bash
cbs catalog build                                               # first-time setup (~5 min)
cbs rag enrich && cbs rag index                                 # build semantic search (~45 min)
cbs search "werkloosheid buurt"
cbs describe 86003NED
cbs get 86003NED --geo "Capelle aan den IJssel" --level buurt
```

## RAG evaluation

The eval runs 30 golden queries across 9 CBS topic categories and compares the self-reflective RAG pipeline against plain keyword search:

```bash
python -m cbs_tool.rag.eval                   # full eval, saves JSON to eval_results/
python -m cbs_tool.rag.eval --verbose         # show per-query grader reasoning
python -m cbs_tool.rag.eval --category crime  # single category
python -m cbs_tool.rag.eval --no-compare      # skip keyword baseline
```

Metrics: `recall@1`, `recall@5`, `mean_grade` (0–3), `mean_iters`, and `Delta (RAG − keyword)`.  
Requires `OPENAI_API_KEY` — uses `gpt-4.1-nano` for grading and `text-embedding-3-small` for retrieval.

## Key CBS tables

| Series | ID | Geo | Years |
|---|---|---|---|
| Kerncijfers wijken en buurten | 86165NED | buurt | 1995–2025 |
| Personen met een uitkering | 86003NED | buurt | 2013–2025 |
| Arbeidsdeelname wijken en buurten | 86258NED | buurt | 2019–2024 |
| Nabijheid voorzieningen | 86134NED | buurt | — |

See `SKILL.md` for the full concept guide and example workflows.

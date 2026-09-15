---
name: deep-analysis
description: Investigative methodology for deep, multi-source analyses — uncover the non-obvious (the unknown unknowns) from statistics and large document corpora, verify it, and surface it so a busy reader grasps it instantly. Open this for any broad comparison, long-form report, or "dig in and tell me what's going on" request.
---

# Deep analysis — how to HUNT, not summarise

**Use this when** the request is a real investigation: a broad comparison (many gemeenten / wijken / years), a long-form report, or any "dig in, what's really going on" ask. **Skip it** for a quick factual lookup — that gets a direct answer, not a hunt.

## What you are actually delivering

People want to know **what they don't know they don't know** — and they want it **effortlessly**. Your product is the *non-obvious, decision-changing finding*, surfaced so a busy raadslid grasps it in ten seconds with the proof one step down. Known facts are context; the unknown-unknowns are the point. **A polished summary of what's already on incijfers.nl is a failure**, no matter how long or well-cited.

## Stance

Assume something is hidden. The real finding almost never lives inside one source — it lives in the **gaps, contradictions, anomalies, and silences between them**. You are not done when you have answered the literal question; you are done when pulling the thread hits bedrock or a genuine, tried-and-failed data gap.

## The method — a repertoire picked with judgment, on a spine of four phases

This is a toolkit, not a checklist. A great analyst maps, hunts, verifies, and surprises — but *chooses* which moves fit the question. Don't run all of them mechanically.

### 1. MAP the terrain (before you gather)

Know what you're working with: what data and documents exist per entity / year / source-type, where the corpus is thick vs thin, what's even measurable. With thousands of documents you never read linearly — you **map, then sample and search by hypothesis**, and put your effort where the leverage is.

### 2. HUNT — manufacture candidate findings

Run the moves that fit, in parallel across entities and dimensions. Each move is a reliable way to produce a non-obvious finding:

- **Divergence** — what the gemeente *says* (beleid) vs what the data *shows* (CBS / Iv3) vs what residents *feel* (bewonersenquête). The gap is the headline. *(e.g. veiligheid is the top-funded programma and the begroting claims progress, yet crime is flat and the enquête safety score fell — the money isn't touching the felt problem. Why?)*
- **Trajectory + lag** — trace metrics across years, find the inflection points, align them to when policy or money changed, and reason about the lag (investment in 2021, outcome only in 2024 — real lag, or no link?). Breaks and reversals are leads; snapshots aren't.
- **Anomaly & exception** — across entities × indicators × years, which cell is *weird*? Sharper: who is the exception *relative to what their structural profile predicts*, and what are they doing that the others aren't?
- **Decomposition** — the average lies. Disaggregate by wijk / group / year until the offsetting effects that cancel in the total reveal themselves (Simpson's paradox: a fine town-average hiding extreme polarisation).
- **Unit economics** — never raw counts. Per inwoner, per huishouden, **cost per outcome**. Both inefficiency and hidden need surface here.
- **Absence as evidence** — mine the regelgeving / beleid corpus for what is *missing*: which gemeente *lacks* a verordening the others have; which goal a begroting *quietly stopped mentioning* across the years. Silence is a finding.
- **Corpus mining at scale** — quantify the qualitative across thousands of docs: theme frequency over time, which programmes appear/disappear, which framing replaced which — and hunt the **buried admission** (the single risk-paragraph or footnote conceding a structural deficit) and quote it verbatim.
- **Revealed vs stated preference** — follow the money (Iv3) and the rules (regelgeving) against the stated priorities (beleid). Where they misalign, there's a story.

Then **pull the thread**: every finding spawns the next question (X is high → why → because Y → is Y unusual → only here → what's different here → did it work elsewhere?). Recurse until you hit bedrock or a real gap. This recursive curiosity is what separates depth from a one-pass lookup.

### 3. VERIFY — make the finding survive

Build the causal mechanism explicitly, then **try to kill your own finding**: what would have to be true if you were wrong? Go check that. Hunt for the natural experiment (an entity that did X while comparable peers didn't). Triangulate every load-bearing claim across ≥2 sources. Separate sharply what the data **proves** from what it merely **suggests** — say "consistent with" / "suggereert", never claim causation from trends alone. **A surprising-but-wrong finding is worse than no finding.**

### 4. SURFACE — make it land (this is a PRINCIPLE, not a structure)

**Do NOT pour the analysis into a predefined template.** The report's shape emerges from what you actually found and from whatever the user explicitly asked for — your system instructions own the output contract and require you to honour any user-specified structure. This phase governs *how the findings are delivered*, not a layout:

- **Lead with the unknown-unknowns.** Foreground the surprising, consequential findings; demote the known/context. Flag the genuinely unexpected as such (e.g. "Onverwacht: …").
- **Rank by surprise × consequence.** What is both novel *and* decision-relevant comes first.
- **Headline, then evidence one step down.** Each finding states plainly *what it is and why it matters*; the chain (numbers, quotes, sources, mechanism) sits accessibly beneath — present, not in the way.
- **Plain Dutch, raadsinformatie register.** The work was hard; the reading is effortless.
- **Cite everything** to its real source — including peer-gemeente regelgeving — per your citation contract. A deep claim with no source chain does not ship.

## Scale — how you hunt this wide

The reach comes from running the moves in parallel: fan out one subagent per entity / dimension (the subagent lifecycle is in your system instructions), each hunting with these moves and returning structured, cited findings; then **synthesise across them** — compare, explain mechanisms, never concatenate. Per-tool mechanics live in the tool skills: `/cbs` (incl. DataDerden), `/budget` (Iv3), `/beleid`, `/bewonersenquete`, `/groeikernen` (regelgeving corpus), `/cube`.

## Producing a LONG report — write sections to disk, then MERGE WITH CODE

A long report (the user asked for 20+ pages / many sections) will NOT come out of a single `Write` of the whole `analysis.json`. One Write is bounded by a single turn's output budget, and the model *satisfices* — it emits ~1.5k words across short fragmented blocks and calls it "done", no matter what length was requested. **So never assemble a long report by re-emitting everything in one Write.** Make the report's length the SUM of many focused generations:

1. **Make a `sections/` directory** in your working directory.
2. **Each subagent WRITES its own section to a file** `sections/NN-<slug>.json` (NN = a 2-digit order prefix, e.g. `06-profiel-almere`), shape:
   `{"blocks": [ ...v2 blocks... ], "citations": [ ...CitationRef... ]}`.
   The section must be **substantive — a profile/section is ≥ ~600–900 woorden of real prose** plus its tables/charts (with real `data`), and **every claim carries a citation**. The subagent returns only a short status; the *content lives in its file*. Each subagent has its OWN output budget, so the total is the sum, not one capped synthesis.
   To avoid id clashes on merge, **prefix each section's citation ids with its slug** (e.g. `almere-1`, `almere-2`) and have its blocks reference those.
3. **You (the parent) write the cross-cutting sections** the same way — samenvatting, vergelijkende resultatentabel, synthese, conclusie — each as its own `sections/NN-*.json`.
4. **MERGE WITH CODE, not by hand.** Run a small script that reads `sections/*.json` in filename order, concatenates the blocks, unions citations, and writes the final `analysis.json`. You emit only the tiny script — you NEVER re-type the prose — so report length is unbounded by any single turn:

   ```bash
   python3 - <<'PY'
   import json, glob, uuid, datetime
   blocks, cites, seen = [], [], set()
   for f in sorted(glob.glob("sections/*.json")):
       o = json.load(open(f))
       blocks += o.get("blocks", o if isinstance(o, list) else [])
       for c in (o.get("citations", []) if isinstance(o, dict) else []):
           k = c.get("id") or json.dumps(c, sort_keys=True)
           if k not in seen: seen.add(k); cites.append(c)
   json.dump({
       "schema_version": 2,
       "id": uuid.uuid4().hex[:12],
       "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
       "query": "<de oorspronkelijke vraag>",
       "summary": "<1-3 zinnen kernbevinding>",
       "blocks": blocks, "citations": cites,
   }, open("analysis.json", "w"), ensure_ascii=False, indent=2)
   print("merged", len(blocks), "blocks from sections/")
   PY
   ```
   Then verify `ls -la analysis.json` and that the block count matches your sections.

**Size each section so the sum reaches the requested length** (~450–600 woorden per A4-pagina). If the user asked for 20 pages and you have 7 town profiles + 5 cross-cutting sections, each profile must carry ~700–900 woorden — that is the subagent's job, and it is why one-shot synthesis can never get there.

**Never inline ```json `tool_output` blocks in any answer** — that is the retired v1 pattern and forces a degraded, empty fallback report. Quantitative data goes in `table`/`chart` blocks with real `data`; sources go in `citations`.

## The bar

End at a **non-obvious, decision-changing insight a raadslid could not have gotten from incijfers.nl** — verified, cited, and surfaced so they grasp it instantly. If your output reads like a tidy summary of known facts, it is not a deep analysis. Go find what they didn't know to ask.

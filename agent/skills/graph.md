# `capelle-graph` — finding-graph skill

The finding-graph is the primary artifact of every groeikern analysis run.  Nodes are claims backed by evidence; edges are typed relationships.  The final `analysis.json` (report) is a **linearization** of the connected core — walk the edges, do not invent content absent from the graph.

---

## Environment (set automatically by the platform)

| Variable | Default | Purpose |
|---|---|---|
| `CAPELLE_MESSAGE_ID` | *required* | Routes writes to the correct session |
| `CAPELLE_INTERNAL_URL` | `http://127.0.0.1:8080` | Platform base URL |

---

## The batch command — primary path

For any phase that produces more than one node, write a `findings.json` file and ship it in a single call:

```bash
capelle-graph add ./findings.json
```

### `findings.json` schema

```json
{
  "nodes": [
    {
      "ref":        "grjr",
      "type":       "context",
      "claim":      "GRJR (Gemeenschappelijke Regeling Jeugdhulp Rijnmond) is de regionale inkooporganisatie die namens Capelle gespecialiseerde jeugdhulp inkoopt.",
      "confidence": "high"
    },
    {
      "ref":        "kosten_stijging",
      "type":       "finding",
      "claim":      "Capelle's jeugdzorgkosten stegen 2019-2023 met 34%, ruim boven het landelijk gemiddelde van 18%.",
      "confidence": "high",
      "blocks":     [...],
      "citations":  [...]
    },
    {
      "ref":        "grjr_tarieven",
      "type":       "hypothesis",
      "claim":      "GRJR-tariefstijgingen verklaren mogelijk een deel van de kostenstijging boven het landelijk gemiddelde.",
      "confidence": "low"
    },
    {
      "ref":        "cjg_instroom",
      "type":       "context",
      "claim":      "CJG Capelle is het lokale toegangspunt voor jeugdhulpverwijzingen; poortwachterrol bepaalt instroom bij GRJR."
    }
  ],
  "edges": [
    {
      "source":    "grjr",
      "target":    "kosten_stijging",
      "type":      "controls",
      "rationale": "GRJR bepaalt tarieven en productenportfolio voor gespecialiseerde jeugdhulp in de regio."
    },
    {
      "source":    "grjr_tarieven",
      "target":    "kosten_stijging",
      "type":      "explains",
      "rationale": "Tariefstijgingen kunnen de kostenstijging bovenop volume-effecten verklaren."
    },
    {
      "source":    "cjg_instroom",
      "target":    "kosten_stijging",
      "type":      "depends_on",
      "rationale": "Volume van dure gespecialiseerde hulp hangt af van doorverwijsbeleid van het CJG."
    },
    {
      "source":    "grjr_tarieven",
      "target":    "cjg_instroom",
      "type":      "tensions_with",
      "rationale": "Hogere tarieven geven financiële prikkel tot minder doorverwijzingen, maar zorgplicht beperkt dit."
    }
  ],
  "summary": "Fase 1: institutionele context gelegd. GRJR als kostendriver geïdentificeerd; CJG-poortwachterrol zichtbaar. Volgende fase: tariefvergelijking met 7 groeikernen."
}
```

**Output** (one JSON object to stdout):

```json
{
  "nodes": {
    "grjr":          {"id": "a1b2c3d4e5f6", "status": "created"},
    "kosten_stijging": {"id": "7890abcdef01", "status": "created"},
    "grjr_tarieven": {"id": "23456789abcd", "status": "linked_existing"},
    "cjg_instroom":  {"id": "ef0123456789", "status": "created"}
  },
  "edges": [
    {"source": "a1b2c3d4e5f6", "target": "7890abcdef01", "edge_type": "controls", "status": "created", "id": "..."},
    ...
  ],
  "errors": []
}
```

`"status": "linked_existing"` means the server detected a near-duplicate (≥0.90 normalized similarity) and returned the existing node's id.  The ref-to-id mapping is updated so subsequent edges resolve correctly — no re-submission needed.

---

## Ref resolution rules

1. Every node carries a local `"ref"` string (arbitrary, no spaces).
2. After each node POST the ref is mapped to the server-assigned id.
3. On **409 duplicate** the ref maps to `duplicate_of` — link, do not duplicate.
4. Edge `source`/`target` values are looked up in the ref map.  **If a source or target is not a known ref it is passed through as-is** — this lets you reference real server ids from a previous batch or from `capelle-graph list --compact`.

---

## Edge vocabulary

| Type | Semantics | Rationale requirement |
|---|---|---|
| `causes` | A is a sufficient, named cause of B | **MUST** name the counterfactual: comparison method + assumption (before/after, diff-in-diff over 7-town panel, matched peer). Without this, use `explains`. |
| `explains` | A is a plausible partial explanation for B | 2-4 sentence mechanism + evidence |
| `controls` | A governs or decides the lever for B | Who acts, at what level (Rijk/regio/gemeente/wijk) |
| `tensions_with` | A and B are in tension or trade-off | What the trade-off is |
| `depends_on` | B cannot exist or be interpreted without A | Why the dependency holds |
| `decomposes_into` | A breaks down into components B, C… | The decomposition axis |

Tension edges are **prized** — they reveal insight.  Isolation is a smell: dig or prune.

**Edges carry content.** A `rationale` is an analytical claim the reader will click and read:
2-4 Dutch sentences explaining the MECHANISM and the evidence behind the relationship —
never a restatement of the edge type.

**Nodes are mini-rapporten.** The `claim` is only the headline. Every finding/verification
node's `blocks` must include at least one substantive Dutch prose block (±150-300 woorden:
wat je vond, hoe je het vaststelde — bron/methode/berekening — waarom het ertoe doet, en de
caveats) plus the data that carries it (table/chart/kpi). Bite-sized nodes are a failure.

---

## Node types

| Type | When to use | Evidence rule |
|---|---|---|
| `finding` | An established empirical result | **Requires** `blocks` or `citations` — the server rejects finding/verification nodes without evidence (HTTP 422) |
| `context` | Institutional or structural fact | No evidence requirement — institutional knowledge is acceptable for context nodes |
| `hypothesis` | Plausible but unconfirmed | No evidence required; mark `confidence: low` |
| `verification` | A finding that verifies or refutes a hypothesis | Requires `blocks` or `citations` |

Confidence: `low` / `medium` / `high`.  Status: `proposed` / `supported` / `verified` / `pruned`.

---

## Granular commands (one-offs)

### Add a single node

```bash
capelle-graph add-node \
  --claim "Capelle's OZB-tarief ligt 8% boven het gemiddelde van 7 groeikernen (2024)." \
  --type finding \
  --confidence high \
  --blocks-file ./ozb-blocks.json \
  --citations-file ./ozb-cites.json \
  --agent-label "sub-agent-belastingen"
```

Output on success: `{"node_id": "a1b2c3d4e5f6"}`
Output on duplicate: `{"duplicate_of": "a1b2c3d4e5f6"}` — exit 0, link to the existing node.

### Add a single edge

```bash
capelle-graph add-edge \
  --from a1b2c3d4e5f6 \
  --to   7890abcdef01 \
  --type causes \
  --rationale "OZB-tarief boven gemiddelde: voor Capelle +8%, voor de zeven peers gemiddeld 0% (diff-in-diff 2019-2024, aanname: gelijkblijvende samenstelling grondslag)."
```

### Set a phase summary

```bash
capelle-graph set-summary \
  --text "Fase 2: kostenanalyse jeugdzorg. 6 bevindingen, 4 context-knooppunten, 9 edges waaronder 2 tension-edges. GRJR als dominante kostendriver bevestigd; OZB-link zwak."
```

### List the current graph

```bash
capelle-graph list              # full graph JSON
capelle-graph list --compact    # id + claim + node_type per node; edges as triples
```

Use `list --compact` **before adding nodes** to check for near-duplicates.  The server-side dedup guard (ratio ≥ 0.90) catches most cases, but pre-checking avoids wasted 409 round-trips.

---

## Dedup discipline (link-don't-duplicate)

1. Before adding nodes in a new sub-agent: `capelle-graph list --compact` and scan for existing claims.
2. If a match exists, reference its id directly in edges (`"source": "<existing-id>"`) — no re-submission.
3. If the server returns 409, the batch command automatically maps the ref to `duplicate_of`.  The granular `add-node` prints `{"duplicate_of": ...}` — use that id for subsequent edges.
4. Cross-agent edges are encouraged: a sub-agent's finding can connect to the lead's context node using the real id.

---

## Research method obligations (from the groeikern prompt)

These rules are also enforced inline in the groeikern system prompt.  The skill is the long-form reference.

- **Landscape first**: on any deep question, gather institutional context nodes + `controls` edges BEFORE interpreting numbers.  The GRJR class of fact (who acts, who controls the lever, at what level) must be on the graph before analysis.
- **Edge-finding is mandatory**: every new node is immediately interrogated — what causes it, what explains it, who controls it, what tensions with it, what depends on it.  An isolated node means dig or prune.
- **Causal discipline**: `causes` requires a named counterfactual (comparison + assumption; the 7 groeikernen are the donor pool — before/after, diff-in-diff, matched peer).  Without one, use `explains`.
- **No null nodes, no padding**: a finding with no evidence is invalid (the server enforces this with HTTP 422).  A search that found nothing produces NO node.
- **Sub-agents write their own nodes**: include `capelle-graph add` instructions in every spawned sub-agent prompt.  This is how the graph accumulates substance across many completions.
- **Set summary after each major phase**: keep the user's graph-view informed.

---

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (including duplicate-already-linked) |
| 2 | Usage error or file read failure |
| 3 | Server unreachable, graph feature disabled (`CAPELLE_GRAPH_ENABLED=false`), or all batch items failed |

Partial batch failure (some items failed, some succeeded) exits 0.

---

## Blocks and citations format

These reuse the existing v2 capelle-report schemas.  A `blocks` list entry example:

```json
{"type": "prose", "content": "Capelle's jeugdzorguitgaven bedroegen €12.4M in 2023, een stijging van 34% t.o.v. 2019."}
```

A `citations` entry example:

```json
{"id": "iv3-2023", "source": "iv3", "doc_type": "data", "document": "CBS Iv3 2023 taakveld 652"}
```

For `finding` / `verification` nodes, at least one block or citation is required by the server.  Context and hypothesis nodes may have empty blocks/citations.

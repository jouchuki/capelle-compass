---
description: How to decompose an analysis across parallel sub-agents — when to fan out, exactly how to spawn, and how to synthesize. Always loaded in groeikern mode.
argument-hint: (auto-loaded; no arguments)
---

# Fan-out — give each concept its own agent

You have an `Agent` tool that runs a sub-agent **in parallel, in its own fresh
context window**, and returns its findings. This is not a last resort for when
you run out of context — it is how a good analyst works: one mind per concept,
thinking hard about it alone, instead of one mind juggling six.

## The trigger — decide right after you plan

Once you've split the question into sub-questions, look at the dependencies:

- **Independent branches** (they do NOT need each other's results) → **fan out:
  one `Agent` per branch, in parallel.** This is the strong default. Two or more
  independent branches is your signal.
- **Sequential branches** (one feeds the next) → do them in order, or chain
  agents (spawn B once A returns).
- **A single concept, or a quick factual lookup** → one thread, no agents.

A branch is "worth its own agent" when it would benefit from sole focus: a
distinct zorgtype, a peer to benchmark, a causal hypothesis to test, a
suspicious series to verify, a data source to mine, a mechanism to explain.

## How to spawn (one call per branch)

Call the `Agent` tool with a SELF-CONTAINED prompt — the sub-agent shares your
working directory and tools but knows nothing of your context, so spell it out:

    Agent(
      description: "Per-zorgtype kostenontbinding Capelle 2020-2024",
      prompt: "Je bent een jeugdzorg-data-analist voor Capelle aan den IJssel.
        ONDERZOEK: hoe verdeelt de stijging van de gerealiseerde jeugdzorgkosten
        2020->2024 zich over de zorgtypen (jeugdhulp zonder verblijf wijkteam /
        niet-wijkteam, met verblijf, jeugdbescherming, jeugdreclassering)? Gebruik
        CBS-tabel 83454NED (gerealiseerde kosten) en de Iv3-data in
        ./groeikernen_iv3/. LET OP base-year-aansluiting en of een sprong een
        definitiewijziging/placeholder kan zijn (identieke waarden over jaren).
        LEVER OP: een korte, met bronnen onderbouwde bevinding per zorgtype, plus
        wat je NIET kon vaststellen. Verzin niets."
    )

Spawn all independent branches before you start gathering yourself. Keep each
returned `task_id`.

## Collect and synthesize

1. Monitor with `TaskList` / `TaskGet`.
2. Read each finished branch with `TaskOutput`.
3. **Synthesize across them** — compare, connect mechanisms, resolve
   contradictions. Do NOT concatenate the sub-agent outputs; weave them into one
   storyline. Each agent had its own output budget, so the whole is the sum, not
   one capped pass.
4. ONLY THEN write the single `AnalysisResult` JSON.

## Worked example A — single municipality, multiple concepts

Question: *"Waarom stijgen de jeugdzorgkosten per jongere in Capelle sinds 2020,
en zit die stijging in alle zorgtypen?"*

Plan → four INDEPENDENT branches → four agents, in parallel:
- **Agent 1 — kosten + noemer:** total realised cost trend AND the jongeren-count
  on a *common base year* (align 2020↔2024), so cost-per-jongere is correct.
- **Agent 2 — per zorgtype:** the breakdown above (zonder/met verblijf, JB/JR);
  flag definitional breaks.
- **Agent 3 — verklaring/beleid:** what Capelle's najaarsnota's/begrotingen say
  drives it, treated as *motivated claims to test*, not facts.
- **Agent 4 — verificatie:** independently re-derive the headline numbers (the
  +55% / per-jongere %) from primaries and flag any artifact.

You synthesize: real residual vs naive headline, where the growth concentrates,
which claims survived verification. One thread could not give each of these the
focus it needs — that's why you fan out.

## Worked example B — many entities

Question: *"Vergelijk de jeugdzorguitgaven per inwoner van de zeven groeikernen."*

→ one agent per gemeente (each: per-inwoner reeks 2020-2024 + the local
explanation), plus one agent for the cross-cutting explanatory variables
(armoede, jeugdaandeel). You synthesize the ranking and the why. Seven towns is
seven contexts — do not carry them in one thread.

## Don't over-do it

If the question is genuinely one concept ("hoeveel jongeren in jeugdzorg in
2024?") or a quick lookup, just answer it. Fan-out is for work that decomposes,
which is most real analysis — but not all of it.

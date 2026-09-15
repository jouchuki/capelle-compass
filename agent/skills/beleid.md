---
name: beleid
description: Search municipal policy documents — begrotingen, voorjaarsnota's, najaarsnota's, jaarstukken (2019-2026)
argument-hint: <search query about municipal policy>
---
name: beleid

Search the municipality's policy documents via semantic search.

Query: $ARGUMENTS

## CLI — always use full path

```bash
beleid search "<query>" [--doc-type TYPE] [--year YYYY] [--limit N]
beleid search "<query>" --output-json   # standardized dashboard format
beleid config                             # show index stats
```

## Available document types

| --doc-type | Dutch | Years | Content |
|---|---|---|---|
| begroting | Begroting | 2021–2026 | Annual budget: programs, goals, financials |
| voorjaarsnota | Voorjaarsnota | 2020–2024 | Spring review: progress, amendments |
| najaarsnota | Najaarsnota | 2020–2023 | Autumn review: mid-year corrections |
| jaarstukken | Jaarstukken | 2019–2024 | Annual accounts: actuals vs budget |
| slotwijziging | Slotwijziging | 2024–2025 | Final amendment before year-end |
| bestuursrapportage | Bestuursrapportage | 2025 | Management report |
| kadernota | Kadernota | 2026 | Framework note for next budget |

## Standard workflow

1. `beleid search "<topic>"` — broad search across all doc types
2. Review results — note which doc_type and year are most relevant
3. `beleid search "<topic>" --doc-type <type>` — narrow to one doc type
4. Combine with CBS data: `cbs search "<related stats>"` for quantitative context

## Topic → search terms mapping

| Topic | Dutch search terms |
|---|---|
| Crime / safety | veiligheid, criminaliteit, ondermijning, handhaving, openbare orde |
| Social welfare | sociaal domein, jeugdzorg, bijstand, participatie, WMO |
| Housing | wonen, woningbouw, huisvesting, stadsontwikkeling |
| Education | onderwijs, school, leerplicht |
| Economy | economie, werkgelegenheid, bedrijventerreinen |
| Health | volksgezondheid, GGD, preventie, sport |
| Environment | milieu, duurzaamheid, klimaat, riolering, afval |
| Budget / finance | begroting, lasten, baten, weerstandsvermogen, reserves |

## Output

Returns JSON with results array. Each result includes:
- text: relevant fragment from the policy document
- score: relevance (0-1, higher is better)
- doc_type, year, document, page_number
- source_url: link to the original document (do NOT surface this URL in citations)

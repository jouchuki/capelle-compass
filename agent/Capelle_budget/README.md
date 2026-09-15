# Capelle Budget

Fetches and analyses Capelle aan den IJssel's municipal budget from CBS Iv3 (2010–2026).

## What it does

Pulls raw Iv3 budget data from the CBS API, groups it by taakveld (policy domain), generates AI-driven insights with Anthropic Claude, and produces an HTML dashboard.

## Run

```bash
# From repo root with venv active:
python Capelle_budget/main.py
```

Output lands in `Capelle_budget/output/`:
- `budget.json` — full structured budget data
- `budget.csv` — flat CSV version
- `dashboard.html` — open in browser
- `insights.json` — AI-generated observations
- `years/YYYY.json` — per-year breakdown

## Taakveld reference

| Prefix | Domain |
|---|---|
| `1.x` | Veiligheid (safety) |
| `2.x` | Verkeer & vervoer |
| `4.x` | Onderwijs |
| `5.x` | Sport & cultuur |
| `6.x` | Sociaal domein |
| `7.x` | Volksgezondheid & milieu |
| `8.x` | Ruimtelijke ordening & wonen |

## Dependencies

`httpx`, `anthropic` — both in the shared repo `requirements.txt`.
Requires `ANTHROPIC_API_KEY` in `.env` at the repo root.

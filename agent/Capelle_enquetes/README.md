# Capelle Enquêtes

Biennial resident satisfaction survey reports for Capelle aan den IJssel (2001–2023), produced by I&O Research.

## Contents

12 PDF reports:
```
Bewonersenquete_Capelle_2001.pdf  through  Bewonersenquete_Capelle_2023.pdf
```

All PDFs are text-based (not scanned). Extract with:
```bash
pdftotext Bewonersenquete_Capelle_2023.pdf -
```

## Scraping scripts

| Script | What it does |
|---|---|
| `download_rapports.py` | Downloads PDFs from the Capelle website |
| `explore_page.py` | Explores the survey results page structure |
| `scrape_2023.py` | Scrapes 2023 edition data |

```bash
# Run from repo root with venv active:
python Capelle_enquetes/download_rapports.py
```

## Report themes

Each edition covers: woonbuurt, sociaal leefklimaat, fysieke kwaliteit, voorzieningen, veiligheid, overlast, gemeentebestuur, dienstverlening.

Scores are rapportcijfers (1–10 scale) per wijk and at gemeente level.

## Wijken reference

```
1a  Capelle West       5   Oostgaarde Noord
1b  's Gravenland      6   Schenkel
2   Middelwatering     7   Schollevaar Zuid
4   Oostgaarde Zuid    8   Schollevaar Noord
                       9   Fascinatio (from ~2007)
```

## Analysing with Claude Code

Use the `/bewonersenquete` skill in Claude Code to analyse themes and trends across years.

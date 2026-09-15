---
name: bewonersenquete
description: Analyse Bewonersenquete Capelle (2001-2025) — resident survey trends by wijk and theme
argument-hint: <topic, wijk, or question about resident satisfaction>
---
name: bewonersenquete

Analyse Bewonersenquete data from PDF reports for Capelle aan den IJssel.

Question: $ARGUMENTS

## Output-size discipline — read before running pdftotext

The bewonersenquete PDFs are 200+ pages each. A bare `pdftotext ... -` dumps 1–2 MB
and will overflow the context window. A loose single-word grep can match thousands of lines.

**Rules — enforced, no exceptions:**

1. **Never pipe `pdftotext ... -` straight to stdout.** Always chain a grep with BOTH a specific
   wijk name AND a topic label, e.g.:
   ```bash
   pdftotext ... - | grep -n -i 'Schollevaar' | grep -i 'rapportcijfer'
   ```

2. **No single-letter or single-word patterns.** Before grepping content, gauge volume first:
   ```bash
   pdftotext ... - | grep -c -i '<pattern>'
   ```
   If the count exceeds 50, tighten the pattern before proceeding.

3. **For a specific table**, use `-layout` with tight context flags:
   ```bash
   pdftotext -layout <pdf> - | grep -A3 -B1 -i '<exact table title>'
   ```

4. **For full-text access**, spill to the working directory — never cat the full text into the turn:
   ```bash
   pdftotext -layout <pdf> ./enquete-<year>.txt
   grep -n -i '<pattern>' ./enquete-<year>.txt
   ```

5. **Never run two `pdftotext ... -` (stdout) pipelines in the same turn.** If you need data
   from multiple PDFs, spill each to a file first, then grep the files.

The first-10-pages excerpt (`-f 1 -l 10`) used for table-of-contents discovery is exempt —
it is small by design and safe to pipe to stdout.

## Data source

13 biennial resident survey PDFs (2001–2025) in `Capelle_enquetes/`:

```
ls Capelle_enquetes/Bewonersenquete_Capelle_*.pdf
```

These are **text-based PDFs** (not scanned), produced by I&O Research.
Text extraction with `pdftotext` works well. No OCR needed.

## Important: structure varies across years

Chapter order, page numbers, and exact section names shift between editions.
**Never hardcode page numbers.** Always discover them dynamically per PDF.

### Step 1 — Discover structure per PDF

For each year you need, first extract the table of contents:

```bash
# Extract first 10 pages to find inhoudsopgave + samenvatting
pdftotext -f 1 -l 10 Capelle_enquetes/Bewonersenquete_Capelle_2015.pdf -
```

This gives you:
- The **inhoudsopgave** with chapter titles and page numbers for that specific edition
- The **samenvatting** with all headline rapportcijfers

### Step 2 — Find the right pages by searching, not guessing

Use grep to locate the chapter you need:

```bash
# Find which page a topic starts on — always use a specific multi-word pattern
pdftotext Capelle_enquetes/Bewonersenquete_Capelle_2015.pdf - | grep -n -i "veiligheid en overlast"

# Or dump full text and grep the file (preferred for broader searches)
pdftotext Capelle_enquetes/Bewonersenquete_Capelle_2015.pdf ./enquete_2015.txt
grep -n -i "rapportcijfer" ./enquete_2015.txt
```

Then extract just that section once you know the pages.

### Step 3 — Multi-year / trend comparison (read EACH year's DATA, not just the samenvatting)

The samenvatting (`-f 1 -l 10`, first ~10 pages) carries only headline gemeente averages —
NOT the per-wijk / per-indicator detail, which sits in the chapters (≈ page 12 onward). For a
trend or comparison TABLE you MUST reach the data for EVERY year in the table:

1. **Mine the latest edition first — it already contains the trend.** The newest report states
   prior-year figures inline (e.g. *"onderhoud van 6,7 in 2021 naar 7,4 in 2025"*) and shows
   `2021 2023 2025` columns in its tables. Spill it `-layout` and grep the indicator — you
   usually get all years from this ONE file without opening the older PDFs:
   ```bash
   pdftotext -layout Capelle_enquetes/Bewonersenquete_Capelle_2025.pdf ./enquete-2025.txt
   grep -n -i -A6 'rapportcijfer buurt in het algemeen' ./enquete-2025.txt
   ```
2. **If a year/indicator isn't in the latest edition, read THAT year's data chapter** — spill
   it `-layout` and grep the indicator. Do NOT stop at `-f 1 -l 10`: that is the samenvatting;
   the per-wijk/per-indicator data is PAST it (the table you want is often ~2 pages further).
3. **A value you did not actually read is `n.b.`** — never inferred, never carried over from
   another year, never written as a comparison (`hoger dan 2023`, `toegenomen t.o.v. 2019`,
   `circa 33%`). If you couldn't find it, the cell is `n.b.` and the gap goes in `data_gaps`.
   **Fabricating a figure is a hard failure.**

Read every year you compare to the SAME depth — never deep-read one year and skim the rest.

### Step 4 — Extract wijk-level data

Reports use numbered wijken. Names are mostly consistent but may vary slightly in older editions:

```
1a  Capelle West
1b  's Gravenland
2   Middelwatering (West/Oost split in later years)
3   Middelwatering Oost (may not exist in early years)
4   Oostgaarde Zuid
5   Oostgaarde Noord
6   Schenkel
7   Schollevaar Zuid
8   Schollevaar Noord
9   Fascinatio (from ~2007 onwards)
```

Grep for wijk scores — the named-wijk alternation is specific enough to stay within the 50-match
rule (typically 9 rows per table), so piping to stdout is acceptable here:

```bash
pdftotext -layout Capelle_enquetes/Bewonersenquete_Capelle_2015.pdf - | grep -E "(Capelle West|Gravenland|Middelwatering|Oostgaarde|Schenkel|Schollevaar|Fascinatio|Gemeente totaal)" | head -40
```

For a single wijk across all years, spill each PDF first to avoid running multiple stdout pipelines:

```bash
for year in 2001 2003 2005 2007 2009 2011 2013 2015 2017 2019 2021 2023 2025; do
  pdftotext -layout Capelle_enquetes/Bewonersenquete_Capelle_$year.pdf ./enquete-$year.txt
done
grep -n -i 'Schollevaar' ./enquete-2015.txt | grep -i 'rapportcijfer'
```

## Typical report themes (naming may vary per edition)

| Theme | What to search for | Typical data |
|---|---|---|
| Woonbuurt | woonbuurt, woonomgeving, buurt in het algemeen | rapportcijfer 1-10 per wijk |
| Sociaal leefklimaat | sociaal, leefklimaat, buurtcontact, saamhorigheid | rapportcijfer or % |
| Fysieke kwaliteit | fysiek, onderhoud, groen, schoon, straatverlichting, parkeren | rapportcijfer per aspect |
| Voorzieningen | voorzieningen, winkels, scholen, speelgelegenheid | rapportcijfer per voorziening |
| Betrokkenheid | betrokkenheid, participatie, inspraak, WOP | % actief |
| Veiligheid | veiligheid, onveilig, criminaliteit | rapportcijfer + % onveilig voelen |
| Overlast | overlast, vandalisme, jongeren, geluidsoverlast, drugs | rapportcijfer per type overlast |
| Slachtofferschap | slachtoffer, aangifte, diefstal, inbraak | % slachtoffer per delict |
| Gemeentebestuur | gemeentebestuur, bestuur, gemeenteraad | rapportcijfer + % tevredenheid |
| Dienstverlening | dienstverlening, contact, balie, telefoon | rapportcijfer |
| Algemeen | leven in Capelle, rapportcijfer gemeente | rapportcijfer overall |
| Vrijwilligerswerk | vrijwilliger, mantelzorg | % actief |
| Prioriteiten | zakken met geld, prioriteit | ranking of themes |

## Cross-reference with other data sources

Connect findings to CBS and budget data where relevant:

```bash
# CBS: neighbourhood-level stats for the same topic
cbs search "<relevant concept>" --level wijk

# Budget: spending on the related taakveld — read the Iv3 CSVs in
# ./groeikernen_iv3/<gemeente>/iv3/<jaar>/data.csv (filter to taakvelden,
# sum Lasten; never sum lasten+baten). See the groeikern budget rules.
grep -h '"L' ./groeikernen_iv3/capelle-aan-den-ijssel/iv3/2024/data.csv | head

# BuitenBeter: recent resident complaints for the same theme
capelle-buitenbeter query --category "<categorie>" --limit 20
```

## Output format

Present findings as:

1. **Bevinding** — what the survey data shows (specific rapportcijfers, percentages per wijk)
2. **Trend** — development over the years (improving / stable / declining), cite specific numbers per year
3. **Wijk-vergelijking** — which wijken score best and worst, any notable wijk-level changes
4. **Opvallend** — surprising jumps, drops, or outliers in the data
5. **Link met beleid** — connect to CBS stats or budget taakvelden if relevant
6. **Data gaps** — note if question wording or wijk groupings changed between editions

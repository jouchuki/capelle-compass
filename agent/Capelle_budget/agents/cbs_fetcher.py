from __future__ import annotations
import asyncio
import json
from pathlib import Path
import httpx
from agents.base import BaseAgent, Skill
from config import OUTPUT_DIR

# One CBS table per year — Gemeenten-level tables, verified against dataderden.cbs.nl
# Pre-2019: old BBV format uses FunctieKostenplaatsBalanspost + k_1stePlaatsing_1/k_2dePlaatsing_2
# 2019+: new BBV format uses TaakveldBalanspost + k_1ePlaatsing_1/k_2ePlaatsing_2
YEAR_TABLES = {
    2010: ("45007NED", "2010X000"),
    2011: ("45008NED", "2011X000"),
    2012: ("45001NED", "2012X000"),
    2013: ("45004NED", "2013X000"),
    2014: ("45005NED", "2014X000"),
    2015: ("45006NED", "2015X000"),
    2016: ("45031NED", "2016X000"),
    2017: ("45038NED", "2017X000"),
    2018: ("45042NED", "2018X000"),
    2019: ("45046NED", "2019X000"),
    2020: ("45050NED", "2020X000"),
    2021: ("45054NED", "2021X000"),
    2022: ("45059NED", "2022X000"),
    2023: ("45063NED", "2023X000"),
    2024: ("45067NED", "2024X000"),
    2025: ("45071NED", "2025X000"),
    2026: ("45078NED", "2026X000"),
}

GEMEENTE  = "GM0502   "   # Capelle aan den IJssel — trailing spaces required
CACHE_DIR = Path(OUTPUT_DIR) / "years"

TAAKVELD_MAP = {
    "0.1": "Governance", "0.2": "Civil affairs", "0.3": "Property management",
    "0.4": "Overhead", "0.5": "Treasury", "0.61": "OZB residential",
    "0.62": "OZB commercial", "0.63": "Parking tax", "0.64": "Other taxes",
    "0.7": "General municipal grant", "0.8": "Other income/expenditure",
    "0.10": "Reserve mutations", "0.11": "Result",
    "1.1": "Fire & crisis management", "1.2": "Public order & safety",
    "2.1": "Traffic & transport", "2.2": "Parking", "2.5": "Public transport",
    "3.1": "Economic development", "3.2": "Business infrastructure",
    "3.3": "Business services", "3.4": "Economic promotion",
    "4.1": "Primary education", "4.2": "Education housing",
    "4.3": "Education policy", "5.1": "Sports policy",
    "5.2": "Sports facilities", "5.3": "Culture", "5.4": "Museums",
    "5.5": "Heritage", "5.6": "Media", "5.7": "Public green space",
    "6.1": "Community & participation", "6.2": "Neighbourhood teams",
    "6.3": "Income support", "6.4": "Care guidance", "6.5": "Employment",
    "6.6": "Wmo provisions", "6.71": "Individual care 18+",
    "6.72": "Individual care 18-", "6.81": "Escalated care 18+",
    "6.82": "Escalated care 18-", "7.1": "Public health",
    "7.2": "Sewage", "7.3": "Waste management", "7.4": "Environment",
    "7.5": "Cemeteries", "8.1": "Spatial planning",
    "8.2": "Land development", "8.3": "Housing & construction",
}


PAGE_SIZE = 2000  # stay under hard server limit for all table versions

def _fetch_year_sync(year: int) -> list:
    # CBS ODataApi rejects `$skip` ("Skip query option not supported");
    # paginate via the server-supplied `odata.nextLink` instead.
    table_id, verslagsoort = YEAR_TABLES[year]
    base_url   = f"https://dataderden.cbs.nl/ODataApi/OData/{table_id}/TypedDataSet"
    filter_str = f"Gemeenten eq '{GEMEENTE}' and Verslagsoort eq '{verslagsoort}'"
    records = []
    url = base_url
    params = {"$filter": filter_str, "$top": str(PAGE_SIZE)}
    with httpx.Client(timeout=60) as client:
        while url:
            resp = client.get(url, params=params if params else None)
            resp.raise_for_status()
            data = resp.json()
            records.extend(data.get("value", []))
            url = data.get("odata.nextLink")
            params = None  # nextLink carries every query option
    return records


def _to_float(val) -> float:
    try:
        return float(val) if val not in (None, ".", "") else 0.0
    except (ValueError, TypeError):
        return 0.0


class CBSFetcherAgent(BaseAgent):
    name = "cbs_fetcher"

    def _register_skills(self):
        return [
            Skill("fetch_iv3",      "Fetch Iv3 data for all years from CBS.", self._fetch_iv3),
            Skill("to_memory_rows", "Convert CBS records to memory row format.", self._to_memory_rows),
        ]

    async def run(self) -> dict:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        all_rows: list = []
        years_loaded: list = []

        for year in sorted(YEAR_TABLES.keys()):
            cache_file = CACHE_DIR / f"{year}.json"
            if cache_file.exists():
                self.log(f"{year}: loading from cache")
                rows = json.loads(cache_file.read_text())
            else:
                self.log(f"{year}: fetching from CBS ...")
                try:
                    records = await asyncio.to_thread(_fetch_year_sync, year)
                    self.log(f"{year}: {len(records)} records")
                    rows = await self._to_memory_rows(records, year)
                    cache_file.write_text(json.dumps(rows))
                except Exception as exc:
                    self.log(f"{year}: failed — {exc}")
                    continue

            all_rows.extend(rows)
            years_loaded.append(year)

        if not all_rows:
            self.log("No data returned for any year")
            return {"status": "error", "rows": 0}

        self.memory.set("parsed_rows", all_rows)
        self.log(f"Loaded {len(all_rows)} rows across years: {years_loaded}")
        return {"status": "ok", "rows": len(all_rows), "years": years_loaded}

    async def _fetch_iv3(self):
        pass  # logic lives in run()

    async def _to_memory_rows(self, records: list, year: int) -> list:
        rows = []
        for rec in records:
            # Handle both old (pre-2019) and new (2019+) CBS Iv3 column formats.
            # k_1ePlaatsing_1 / k_2ePlaatsing_2 are two budget-stage placements
            # (primair vs gewijzigd), NOT lasten vs baten. Which ledger side the
            # row hits is determined by the Categorie prefix:
            #   L* -> Lasten (expense)
            #   B* -> Baten (revenue)
            #   A*/P* -> programma/algemene header rows — not transactions, skip.
            if "TaakveldBalanspost" in rec:
                taakveld = str(rec.get("TaakveldBalanspost", "")).strip()
                value    = _to_float(rec.get("k_1ePlaatsing_1", 0)) * 1000
            else:
                # Old format: FunctieKostenplaatsBalanspost + k_1stePlaatsing_1
                taakveld = str(rec.get("FunctieKostenplaatsBalanspost", "")).strip()
                value    = _to_float(rec.get("k_1stePlaatsing_1", 0)) * 1000
            categorie = str(rec.get("Categorie", "")).strip()
            if categorie.startswith("L"):
                lasten, baten = value, 0.0
            elif categorie.startswith("B"):
                lasten, baten = 0.0, value
            else:
                # A*/P* aggregation rows — not real transactions, skip entirely.
                continue
            label = TAAKVELD_MAP.get(taakveld, f"Functie {taakveld}" if year < 2019 else f"Taakveld {taakveld}")
            rows.append({
                "programma":    label,
                "omschrijving": label,
                "taakveld":     taakveld,
                "categorie":    categorie,
                "jaar":         year,
                "lasten_num":   lasten,
                "baten_num":    baten,
                "_source":      f"CBS Iv3 {YEAR_TABLES[year][0]}",
                "_type":        "cbs_iv3",
            })
        return rows

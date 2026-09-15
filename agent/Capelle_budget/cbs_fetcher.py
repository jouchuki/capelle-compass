from __future__ import annotations
import asyncio
import httpx
from agents.base import BaseAgent, Skill

# Each year is a separate CBS table
YEAR_TABLES = {
    2023: ("45063NED", "2023X000"),
    2024: ("45067NED", "2024X000"),
    2025: ("45071NED", "2025X000"),
}

GEMEENTE = "GM0502   "  # Capelle aan den IJssel — trailing spaces required

TAAKVELD_LABELS = {
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


def _fetch_year_sync(year: int) -> list[dict]:
    """Fetch one year synchronously — runs in a thread."""
    table_id, verslagsoort = YEAR_TABLES[year]
    url = f"https://dataderden.cbs.nl/ODataApi/OData/{table_id}/TypedDataSet"
    filter_str = f"Gemeenten eq '{GEMEENTE}' and Verslagsoort eq '{verslagsoort}'"
    params = {"$filter": filter_str, "$top": "10000"}
    records = []
    with httpx.Client(timeout=60) as client:
        while url:
            resp = client.get(url, params=params if params else None)
            resp.raise_for_status()
            data = resp.json()
            records.extend(data.get("value", []))
            url = data.get("odata.nextLink")
            params = None
    return records


def _to_float(val) -> float:
    try:
        return float(val) if val not in (None, ".", "") else 0.0
    except (ValueError, TypeError):
        return 0.0


class CBSFetcherAgent(BaseAgent):
    name = "cbs_fetcher"

    def _register_skills(self) -> list:
        return [
            Skill(
                name="fetch_iv3",
                description="Fetch Iv3 budget data for all years from CBS API.",
                fn=self._fetch_iv3,
            ),
            Skill(
                name="to_memory_rows",
                description="Convert CBS records to memory row format.",
                fn=self._to_memory_rows,
            ),
        ]

    async def run(self) -> dict:
        self.log(f"Fetching CBS Iv3 data for years: {list(YEAR_TABLES.keys())}")
        all_rows = []

        for year in YEAR_TABLES:
            self.log(f"Fetching {year} ...")
            try:
                records = await asyncio.to_thread(_fetch_year_sync, year)
                self.log(f"  {year}: {len(records)} records from CBS")
                rows = await self.get_skill("to_memory_rows")(records, year)
                all_rows.extend(rows)
            except Exception as exc:
                self.log(f"  {year}: failed — {exc}")

        if not all_rows:
            self.log("No data returned — falling back to synthetic data")
            return {"status": "error", "rows": 0}

        self.memory.set("parsed_rows", all_rows)
        years_loaded = sorted(set(r["jaar"] for r in all_rows))
        self.log(f"Loaded {len(all_rows)} rows across years: {years_loaded}")
        return {"status": "ok", "rows": len(all_rows), "years": years_loaded}

    async def _fetch_iv3(self) -> list[dict]:
        # Not used directly — run() calls _fetch_year_sync per year
        return []

    async def _to_memory_rows(self, records: list[dict], year: int) -> list[dict]:
        rows = []
        for rec in records:
            taakveld = str(rec.get("TaakveldBalanspost", "")).strip()
            label = TAAKVELD_LABELS.get(taakveld, f"Taakveld {taakveld}")
            lasten = _to_float(rec.get("k_1ePlaatsing_1", 0)) * 1000
            baten = _to_float(rec.get("k_2ePlaatsing_2", 0)) * 1000
            rows.append({
                "programma":    label,
                "omschrijving": label,
                "taakveld":     taakveld,
                "categorie":    str(rec.get("Categorie", "")).strip(),
                "jaar":         year,
                "lasten_num":   lasten,
                "baten_num":    baten,
                "_source":      f"CBS Iv3 {YEAR_TABLES[year][0]}",
                "_type":        "cbs_iv3",
            })
        return rows

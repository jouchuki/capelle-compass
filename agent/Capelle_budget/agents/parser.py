cat > agents/parser.py << 'EOF'
from __future__ import annotations
import io, re
from agents.base import BaseAgent, Skill

def _clean_amount(raw):
    s = re.sub(r"[€\s]", "", raw).replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None

class ParserAgent(BaseAgent):
    name = "parser"

    def _register_skills(self):
        return [
            Skill("pdf_extract",   "Extract tables from a PDF blob in memory.",   self._pdf_extract),
            Skill("table_parse",   "Parse HTML tables from a stored HTML page.",   self._table_parse),
            Skill("normalise_row", "Clean and type-coerce a raw budget row dict.", self._normalise_row),
        ]

    async def run(self):
        rows = []
        for url in (self.memory.get("pdf_urls") or []):
            raw = self.memory.get(f"pdf:{url}")
            if raw:
                rows.extend(await self.get_skill("pdf_extract")(url, raw))
        for key in self.memory.keys():
            if key.startswith("html:"):
                rows.extend(await self.get_skill("table_parse")(key[5:], self.memory.get(key)))
        normalised = [r for r in [await self.get_skill("normalise_row")(r) for r in rows] if r]
        self.memory.set("parsed_rows", normalised)
        self.log(f"Parsed {len(normalised)} budget rows")
        return {"status": "ok", "rows_extracted": len(normalised)}

    async def _pdf_extract(self, url, raw):
        self.log(f"pdf_extract {url}")
        try:
            import pdfplumber
        except ImportError:
            self.log("pdfplumber not installed")
            return []
        rows = []
        try:
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                for pn, page in enumerate(pdf.pages, 1):
                    for table in (page.extract_tables() or []):
                        if not table:
                            continue
                        header = [str(c).strip() if c else f"col_{i}" for i,c in enumerate(table[0])]
                        for dr in table[1:]:
                            row = {header[i]: (cell or "").strip() for i,cell in enumerate(dr) if i<len(header)}
                            row.update({"_source": url, "_page": pn, "_type": "pdf"})
                            rows.append(row)
        except Exception as exc:
            self.log(f"pdf_extract error: {exc}")
        return rows

    async def _table_parse(self, url, html):
        self.log(f"table_parse {url}")
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            self.log("beautifulsoup4 not installed")
            return []
        rows = []
        soup = BeautifulSoup(html, "html.parser")
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True) or f"col_{i}" for i,th in enumerate(table.find_all("th"))]
            if not headers:
                continue
            for tr in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if not cells:
                    continue
                row = {headers[i]: cells[i] for i in range(min(len(headers), len(cells)))}
                row.update({"_source": url, "_type": "html"})
                rows.append(row)
        return rows

    async def _normalise_row(self, row):
        AMOUNT_KEYS = re.compile(r"(bedrag|lasten|baten|saldo|budget|kosten|opbrengst|totaal)", re.IGNORECASE)
        if all(v == "" for k,v in row.items() if not k.startswith("_")):
            return None
        out = dict(row)
        for k, v in row.items():
            if k.startswith("_"):
                continue
            if AMOUNT_KEYS.search(k):
                cleaned = _clean_amount(str(v))
                if cleaned is not None:
                    out[f"{k}_num"] = cleaned
        return out
EOF
from __future__ import annotations
import re, httpx
from agents.base import BaseAgent, Skill
from config import BUDGET_LINK_KEYWORDS
BUDGET_KEYWORDS = re.compile("|".join(BUDGET_LINK_KEYWORDS), re.IGNORECASE)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MunicipalityBudgetBot/1.0)"}
class ScraperAgent(BaseAgent):
    name = "scraper"
    def _register_skills(self):
        return [
            Skill("web_fetch",      "Fetch HTML from a URL and store in memory.",       self._web_fetch),
            Skill("pdf_download",   "Download a PDF and store bytes in memory.",         self._pdf_download),
            Skill("discover_links", "Find budget-related links on a fetched HTML page.", self._discover_links),
        ]
    async def run(self, start_url):
        self.log(f"Starting at {start_url}")
        html = await self.get_skill("web_fetch")(start_url)
        if not html: return {"status": "error", "reason": "Could not fetch start URL"}
        links = await self.get_skill("discover_links")(start_url, html)
        self.log(f"Found {len(links)} budget-related links")
        pdf_count = 0
        for url in links:
            if url.lower().endswith(".pdf"): await self.get_skill("pdf_download")(url); pdf_count += 1
            else: await self.get_skill("web_fetch")(url)
        return {"status": "ok", "start_url": start_url, "links_found": len(links), "pdfs_downloaded": pdf_count, "pages_fetched": len(links) - pdf_count}
    async def _web_fetch(self, url):
        self.log(f"web_fetch {url}")
        try:
            async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as client:
                resp = await client.get(url); resp.raise_for_status()
                self.memory.set(f"html:{url}", resp.text); return resp.text
        except Exception as exc: self.log(f"web_fetch failed: {exc}"); return None
    async def _pdf_download(self, url):
        self.log(f"pdf_download {url}")
        try:
            async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=60) as client:
                resp = await client.get(url); resp.raise_for_status()
                self.memory.set(f"pdf:{url}", resp.content); self.memory.append("pdf_urls", url); return resp.content
        except Exception as exc: self.log(f"pdf_download failed: {exc}"); return None
    async def _discover_links(self, base_url, html):
        from urllib.parse import urljoin, urlparse
        from html.parser import HTMLParser
        class LP(HTMLParser):
            def __init__(self): super().__init__(); self.links = []
            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    for attr, val in attrs:
                        if attr == "href" and val: self.links.append(val)
        p = LP(); p.feed(html)
        base_parsed = urlparse(base_url)
        results, seen = [], set()
        for raw in p.links:
            full = urljoin(base_url, raw); parsed = urlparse(full)
            if parsed.netloc != base_parsed.netloc or full in seen: continue
            if BUDGET_KEYWORDS.search(full): seen.add(full); results.append(full)
        self.memory.set(f"discovered_links:{base_url}", results); return results

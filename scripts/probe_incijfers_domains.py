#!/usr/bin/env python3
"""Probe municipality incijfers.nl dashboard domains using Scrapling.

Input:  data/nederlandse_gemeenten.json
Output: data/incijfers_domain_probe.json + .csv
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scrapling.fetchers import AsyncFetcher

BASE = Path(__file__).resolve().parents[1]
INPUT = BASE / "data" / "nederlandse_gemeenten.json"
OUT_JSON = BASE / "data" / "incijfers_domain_probe.json"
OUT_CSV = BASE / "data" / "incijfers_domain_probe.csv"

TIMEOUT = 8
CONCURRENCY = 16

STOPWORDS_FOR_SHORT = {"de", "den", "het", "'s", "s", "sint", "st"}

# Known marketing/legacy aliases where the dashboard subdomain is not the exact full municipality name.
# Keep this small: the probe still records all generic candidates.
KNOWN_EXTRA_CANDIDATES = {
    "Capelle aan den IJssel": ["capelle"],
    "'s-Gravenhage": ["denhaag", "den-haag", "sgravenhage", "s-gravenhage"],
    "Haarlemmermeer": ["haarlemmermeer"],
    "Krimpenerwaard": ["krimpenerwaard"],
    "Nissewaard": ["nissewaard"],
}


def slug_base(name: str) -> str:
    s = name.strip().lower()
    s = s.replace("&", " en ")
    s = s.replace("’", "'").replace("`", "'")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    # Convert leading 's- into s- and keep it candidate-friendly.
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def candidates_for(name: str) -> list[str]:
    full = slug_base(name)
    words = [w for w in full.split("-") if w]
    out: list[str] = []

    def add(x: str | None) -> None:
        if not x:
            return
        x = x.strip("-").lower()
        if not x or len(x) < 2:
            return
        if x not in out:
            out.append(x)

    for c in KNOWN_EXTRA_CANDIDATES.get(name, []):
        add(c)
    add(full)
    add(full.replace("-", ""))

    # Common Incijfers pattern: shortened city/brand name, e.g. capelle.incijfers.nl.
    if words and words[0] not in STOPWORDS_FOR_SHORT and len(words[0]) >= 4:
        add(words[0])

    # Prefix before connectors: Capelle aan den IJssel -> capelle, Alphen aan den Rijn -> alphen.
    connectors = {"aan", "op", "bij", "onder", "boven", "in", "den", "de", "het"}
    prefix = []
    for w in words:
        if w in connectors and prefix:
            break
        prefix.append(w)
    if prefix and prefix != words:
        add("-".join(prefix))
        add("".join(prefix))

    # Alternative without articles often used in hostnames.
    without_articles = [w for w in words if w not in {"de", "den", "het", "aan"}]
    if without_articles != words:
        add("-".join(without_articles))
        add("".join(without_articles))

    return out


def classify_response(url: str, response: Any, gemeente: str) -> dict[str, Any]:
    status = getattr(response, "status", None)
    final_url = str(getattr(response, "url", "") or "")
    try:
        title = response.css("title::text").get() or ""
    except Exception:
        title = ""
    title = " ".join(str(title).split())
    lower_title = title.lower()
    lower_final = final_url.lower()

    # invalid wildcard domains usually land here with HTTP 200.
    is_available_domains = "availabledomains" in lower_final or lower_title == "gemeente in cijfers"
    is_404 = status == 404 or "pagina niet gevonden" in lower_title
    has_dashboard_title = " in cijfers" in lower_title and not is_available_domains and not is_404
    final_is_dashboard = "/dashboard" in lower_final and not is_available_domains and not is_404
    ok = bool(status == 200 and has_dashboard_title and final_is_dashboard)

    return {
        "url": url,
        "status": status,
        "final_url": final_url,
        "title": title,
        "ok": ok,
        "reason": "ok" if ok else ("available_domains" if is_available_domains else ("404" if is_404 else "not_dashboard")),
    }


async def fetch_one(sem: asyncio.Semaphore, url: str, gemeente: str) -> dict[str, Any]:
    async with sem:
        try:
            response = await AsyncFetcher.get(url, timeout=TIMEOUT)
            return classify_response(url, response, gemeente)
        except Exception as e:
            return {
                "url": url,
                "status": None,
                "final_url": "",
                "title": "",
                "ok": False,
                "reason": type(e).__name__,
                "error": str(e)[:300],
            }


async def probe_record(sem: asyncio.Semaphore, rec: dict[str, Any]) -> dict[str, Any]:
    name = rec["name"]
    cand = candidates_for(name)
    attempts = []
    match = None
    for slug in cand:
        url = f"https://{slug}.incijfers.nl/Dashboard"
        result = await fetch_one(sem, url, name)
        attempts.append({"slug": slug, **result})
        if result["ok"]:
            match = attempts[-1]
            break
    return {
        "name": name,
        "cbs_code": rec.get("cbs_code"),
        "province": rec.get("province"),
        "special_municipality": rec.get("special_municipality", False),
        "candidate_slugs": cand,
        "found": bool(match),
        "match": match,
        "attempts": attempts,
    }


async def main() -> None:
    logging.getLogger("scrapling").setLevel(logging.WARNING)
    source = json.loads(INPUT.read_text())
    records = source["records"]
    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = [probe_record(sem, rec) for rec in records]
    results = []
    for idx, coro in enumerate(asyncio.as_completed(tasks), 1):
        result = await coro
        results.append(result)
        if idx % 25 == 0:
            found = sum(1 for r in results if r["found"])
            print(f"progress {idx}/{len(records)} found={found}", flush=True)

    results.sort(key=lambda r: (r.get("province") or "", r["name"]))
    payload = {
        "source_municipalities": source.get("source"),
        "probe_target_pattern": "https://{candidate}.incijfers.nl/Dashboard",
        "generated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "municipality_count": len(records),
        "found_count": sum(1 for r in results if r["found"]),
        "results": results,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "cbs_code", "province", "found", "slug", "url", "final_url", "title", "status", "reason", "candidates_tried"])
        w.writeheader()
        for r in results:
            m = r.get("match") or {}
            last = r["attempts"][-1] if r["attempts"] else {}
            representative = m or last
            w.writerow({
                "name": r["name"],
                "cbs_code": r.get("cbs_code"),
                "province": r.get("province"),
                "found": r["found"],
                "slug": representative.get("slug", ""),
                "url": representative.get("url", ""),
                "final_url": representative.get("final_url", ""),
                "title": representative.get("title", ""),
                "status": representative.get("status", ""),
                "reason": representative.get("reason", ""),
                "candidates_tried": " ".join(a["slug"] for a in r["attempts"]),
            })

    print(f"done municipalities={len(records)} found={payload['found_count']} json={OUT_JSON} csv={OUT_CSV}")
    sample = [r["name"] + "=" + r["match"]["slug"] for r in results if r["found"]][:20]
    print("found sample:", ", ".join(sample))


if __name__ == "__main__":
    asyncio.run(main())

"""
Download historical Bewonersenquete PDF reports for Capelle aan den IJssel.
Source: https://capelle-ijssel.buurtmonitor.nl/mosaic/bewonersenquete-2023/bewonersenquete-2023
"""

import os
import urllib.request

OUTPUT_DIR = os.path.dirname(__file__)

RAPPORTS = {
    2021: "https://capelle-ijssel.buurtmonitor.nl/mosaic/images/Bewonersenquete_Capelle_2021.pdf",
    2019: "https://capelle-ijssel.buurtmonitor.nl/mosaic/images/Bewonersenquete_Capelle_2019.pdf",
    2017: "https://capelle-ijssel.buurtmonitor.nl/mosaic/images/Bewonersenquete_Capelle_2017.pdf",
    2015: "https://capelle-ijssel.buurtmonitor.nl/mosaic/images/Bewonersenquete_Capelle_2015.pdf",
    2013: "https://capelle-ijssel.buurtmonitor.nl/mosaic/images/Bewonersenquete_Capelle_2013.pdf",
    2011: "https://capelle-ijssel.buurtmonitor.nl/mosaic/images/Bewonersenquete_Capelle_2011.pdf",
    2009: "https://capelle-ijssel.buurtmonitor.nl/mosaic/images/Bewonersenquete_Capelle_2009.pdf",
    2007: "https://capelle-ijssel.buurtmonitor.nl/mosaic/images/Bewonersenquete_Capelle_2007.pdf",
    2005: "https://capelle-ijssel.buurtmonitor.nl/mosaic/images/Bewonersenquete_Capelle_2005.pdf",
    2003: "https://capelle-ijssel.buurtmonitor.nl/mosaic/images/Bewonersenquete_Capelle_2003.pdf",
    2001: "https://capelle-ijssel.buurtmonitor.nl/mosaic/images/Bewonersenquete_Capelle_2001.pdf",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def download(year: int, url: str) -> bool:
    filename = f"Bewonersenquete_Capelle_{year}.pdf"
    out_path = os.path.join(OUTPUT_DIR, filename)

    if os.path.exists(out_path):
        print(f"  Skip (exists): {filename}")
        return True

    print(f"  Downloading Rapport {year} ...", end=" ", flush=True)
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        with open(out_path, "wb") as f:
            f.write(data)
        size_kb = len(data) // 1024
        print(f"OK ({size_kb} KB) -> {filename}")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False


def main():
    print(f"Downloading {len(RAPPORTS)} rapport(s) to: {OUTPUT_DIR}\n")
    ok = 0
    for year in sorted(RAPPORTS, reverse=True):
        if download(year, RAPPORTS[year]):
            ok += 1
    print(f"\nDone: {ok}/{len(RAPPORTS)} downloaded.")


if __name__ == "__main__":
    main()

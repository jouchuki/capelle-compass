"""
Auto-discovery scraper for https://capelleaandenijssel.begrotingsapp.nl/

Strategy:
  1. Load the homepage and extract all document-level links (begroting-YYYY,
     voorjaarsnota-YYYY, najaarsnota-YYYY, rekening-YYYY, etc.)
  2. For each document, load its landing page and collect all
     /…/programma/<slug> links from the sidebar navigation.
  3. Download each page as a PDF (skipping already-saved files).

Output:
  pdfs/<Document_Year>/  (e.g. pdfs/Begroting_2026/, pdfs/Voorjaarsnota_2023/)
"""

from playwright.sync_api import sync_playwright, Page
from urllib.parse import urljoin, urlparse
import os
import re
import time

BASE_URL    = "https://capelleaandenijssel.begrotingsapp.nl"
OUTPUT_DIR = os.environ.get("CAPELLE_POLICY_PDF_ROOT", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "policy-pdfs"))
NAV_TIMEOUT = 30_000   # ms
WAIT_AFTER  = 2        # seconds after networkidle before printing


# ── Helpers ───────────────────────────────────────────────────────────────────

def normalise(href: str) -> str | None:
    """Return an absolute URL for *href* if it belongs to BASE_URL, else None."""
    if not href:
        return None
    url = urljoin(BASE_URL, href)
    if not url.startswith(BASE_URL):
        return None
    # Strip query / fragment
    p = urlparse(url)
    return p.scheme + "://" + p.netloc + p.path


def folder_name(doc_path: str) -> str:
    """
    Turn a document root path like '/begroting-2026' into 'Begroting_2026'.
    Works for: begroting, voorjaarsnota, najaarsnota, jaarrekening, rekening,
               perspectiefnota, …
    """
    slug = doc_path.strip("/")          # e.g. "begroting-2026"
    # Split on the last hyphen-followed-by-4-digits
    m = re.match(r"^(.*?)[-_](\d{4})$", slug)
    if m:
        name, year = m.group(1), m.group(2)
        name = name.replace("-", " ").title().replace(" ", "_")
        return f"{name}_{year}"
    # Fallback: capitalise the whole slug
    return slug.replace("-", "_").title()


def url_to_filename(path: str) -> str:
    return path.rstrip("/").split("/")[-1] + ".pdf"


# ── Discovery ─────────────────────────────────────────────────────────────────

def discover_documents(page: Page) -> list[str]:
    """
    Load the homepage and collect unique document root paths
    (e.g. ['/begroting-2026', '/voorjaarsnota-2025', …]).
    """
    print(f"Loading homepage: {BASE_URL}")
    page.goto(BASE_URL, wait_until="networkidle", timeout=NAV_TIMEOUT)
    time.sleep(1)

    # Grab all <a> hrefs on the page
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")

    docs: dict[str, bool] = {}
    for href in hrefs:
        url = normalise(href)
        if not url:
            continue
        path = urlparse(url).path  # e.g. /begroting-2026/programma/aanbiedingsbrief
        # Keep paths that look like /{doc-type}-{year}[/…]
        m = re.match(r"^(/[a-z]+-\d{4})(/|$)", path)
        if m:
            root = m.group(1)
            docs[root] = True

    result = sorted(docs.keys())
    print(f"Found {len(result)} document(s): {result}")
    return result


def discover_pages(page: Page, doc_root: str) -> list[str]:
    """
    Load the first page of a document and collect all
    /…/programma/<slug> paths from the sidebar navigation.
    Returns a deduplicated, ordered list of full URLs.
    """
    # Try the document root; many apps redirect to the first section
    start_url = BASE_URL + doc_root
    print(f"  Discovering pages for {doc_root} …")
    try:
        page.goto(start_url, wait_until="networkidle", timeout=NAV_TIMEOUT)
    except Exception as e:
        print(f"    WARNING: could not load {start_url}: {e}")
        return []
    time.sleep(1)

    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")

    seen: dict[str, bool] = {}
    pages: list[str] = []
    pattern = re.compile(r"^" + re.escape(doc_root) + r"/programma/[^/]+$")

    for href in hrefs:
        url = normalise(href)
        if not url:
            continue
        path = urlparse(url).path
        if pattern.match(path) and path not in seen:
            seen[path] = True
            pages.append(url)

    # If the sidebar wasn't present on the root, try the first programma page
    if not pages:
        alt = BASE_URL + doc_root + "/programma"
        try:
            page.goto(alt, wait_until="networkidle", timeout=NAV_TIMEOUT)
            time.sleep(1)
            hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
            for href in hrefs:
                url = normalise(href)
                if not url:
                    continue
                path = urlparse(url).path
                if pattern.match(path) and path not in seen:
                    seen[path] = True
                    pages.append(url)
        except Exception:
            pass

    print(f"    → {len(pages)} page(s) found")
    return pages


# ── Download ──────────────────────────────────────────────────────────────────

def save_as_pdf(page: Page, url: str, out_path: str) -> bool:
    """Navigate to *url* and save as PDF. Returns True on success."""
    try:
        page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT)
        time.sleep(WAIT_AFTER)
        page.pdf(
            path=out_path,
            format="A4",
            print_background=True,
            margin={"top": "15mm", "bottom": "15mm", "left": "15mm", "right": "15mm"},
        )
        return True
    except Exception as e:
        print(f"    ERROR saving {url}: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="nl-NL",
        )
        page = context.new_page()

        # 1. Discover all documents
        doc_roots = discover_documents(page)
        if not doc_roots:
            print("No documents found — check if the site structure has changed.")
            browser.close()
            return

        # 2. For each document, discover its pages
        doc_pages: dict[str, list[str]] = {}
        for root in doc_roots:
            urls = discover_pages(page, root)
            if urls:
                doc_pages[root] = urls

        total = sum(len(v) for v in doc_pages.values())
        print(f"\nTotal pages to download: {total}\n")

        # 3. Download each page
        done = 0
        skipped = 0
        errors = 0

        for root, urls in doc_pages.items():
            folder = folder_name(root)
            out_dir = os.path.join(OUTPUT_DIR, folder)
            os.makedirs(out_dir, exist_ok=True)
            print(f"\n=== {folder} ({len(urls)} pages) ===")

            for url in urls:
                done += 1
                path = urlparse(url).path
                filename = url_to_filename(path)
                out_path = os.path.join(out_dir, filename)

                if os.path.exists(out_path):
                    print(f"  [{done}/{total}] Skip (exists): {filename}")
                    skipped += 1
                    continue

                print(f"  [{done}/{total}] {url}")
                ok = save_as_pdf(page, url, out_path)
                if ok:
                    print(f"    → Saved: {filename}")
                else:
                    errors += 1

        browser.close()

    print(f"\n{'='*60}")
    print(f"Done!")
    print(f"  Downloaded : {done - skipped - errors}")
    print(f"  Skipped    : {skipped}")
    print(f"  Errors     : {errors}")
    print(f"  PDFs saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()

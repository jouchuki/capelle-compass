"""
Quick exploration script to understand the buurtmonitor page structure
and find the PDF/rapport links for historical bewonersenquetes.
"""

from playwright.sync_api import sync_playwright
import json
import time

URL = "https://capelle-ijssel.buurtmonitor.nl/mosaic/bewonersenquete-2023/bewonersenquete-2023"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="nl-NL",
        )
        page = context.new_page()

        print(f"Loading: {URL}")
        page.goto(URL, wait_until="networkidle", timeout=60_000)
        time.sleep(3)

        # Dump all links
        hrefs = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({href: e.getAttribute('href'), text: e.innerText.trim()}))"
        )
        print(f"\n=== All links ({len(hrefs)}) ===")
        for a in hrefs:
            print(f"  [{a['text'][:60]}] -> {a['href']}")

        # Look for PDF-related links
        print("\n=== PDF / rapport links ===")
        for a in hrefs:
            h = (a['href'] or '').lower()
            t = (a['text'] or '').lower()
            if '.pdf' in h or 'rapport' in h or 'rapport' in t or 'download' in t:
                print(f"  [{a['text']}] -> {a['href']}")

        # Look for buttons/clickable elements mentioning rapport or year
        buttons = page.eval_on_selector_all(
            "button, [role='button'], [class*='rapport'], [class*='download']",
            "els => els.map(e => ({text: e.innerText.trim(), class: e.className}))"
        )
        print(f"\n=== Buttons/clickable ({len(buttons)}) ===")
        for b in buttons:
            if b['text']:
                print(f"  [{b['text'][:80]}] class={b['class'][:60]}")

        # Look for any text mentioning years 2001-2021
        years = [str(y) for y in range(2001, 2024, 2)]
        for year in years:
            elements = page.query_selector_all(f"*:has-text('{year}')")
            if elements:
                for el in elements[:3]:
                    try:
                        tag = el.evaluate("e => e.tagName")
                        text = el.inner_text()[:100]
                        print(f"  Year {year} found in <{tag}>: {text}")
                    except Exception:
                        pass

        # Also dump page title and any dropdown/select options
        title = page.title()
        print(f"\nPage title: {title}")

        # Check for select/dropdown elements
        selects = page.eval_on_selector_all(
            "select option",
            "els => els.map(e => ({value: e.value, text: e.innerText.trim()}))"
        )
        if selects:
            print(f"\n=== Dropdown options ({len(selects)}) ===")
            for s in selects:
                print(f"  [{s['text']}] = {s['value']}")

        # Check network requests for PDF URLs
        print("\n=== Checking for PDF network requests ===")
        pdf_requests = []

        def handle_request(request):
            if '.pdf' in request.url.lower():
                pdf_requests.append(request.url)

        page.on("request", handle_request)
        page.reload(wait_until="networkidle", timeout=60_000)
        time.sleep(2)

        if pdf_requests:
            print(f"PDF requests intercepted: {pdf_requests}")
        else:
            print("No PDF network requests detected on page load.")

        browser.close()


if __name__ == "__main__":
    main()

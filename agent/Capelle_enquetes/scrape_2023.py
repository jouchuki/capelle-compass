"""
Save the 2023 Bewonersenquete as a PDF.
Strategy:
  1. Trigger the site's "Download alles als PDF" button via JS.
  2. Intercept the PDF response inside the Playwright browser (session cookies included).
  3. Fall back to Playwright page.pdf() renderer if the server PDF is not served.
"""

import os
import time
from playwright.sync_api import sync_playwright

URL = "https://capelle-ijssel.buurtmonitor.nl/mosaic/bewonersenquete-2023/bewonersenquete-2023"
OUTPUT_DIR = os.path.dirname(__file__)
OUT_FILE = os.path.join(OUTPUT_DIR, "Bewonersenquete_Capelle_2023.pdf")


def main():
    if os.path.exists(OUT_FILE):
        print(f"Already exists: {OUT_FILE}")
        return

    pdf_bytes: list[bytes] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="nl-NL",
            accept_downloads=True,
        )
        page = context.new_page()

        # Intercept the PDF response at the network level (inside the session)
        def intercept_response(response):
            ct = response.headers.get("content-type", "")
            if "pdf" in ct or response.url.endswith(".pdf"):
                try:
                    pdf_bytes.append(response.body())
                    print(f"  Intercepted PDF response from: {response.url}")
                except Exception:
                    pass

        page.on("response", intercept_response)

        print(f"Loading: {URL}")
        page.goto(URL, wait_until="networkidle", timeout=60_000)
        time.sleep(3)

        downloaded = False

        # Attempt 1: trigger download via JS, intercept as download event
        try:
            print("Triggering site download button via JS ...")
            page.evaluate("""
                () => {
                    const btns = [...document.querySelectorAll('button')];
                    const menu = btns.find(b => b.className && b.className.includes('utils-menu'));
                    if (menu) { menu.click(); }
                }
            """)
            time.sleep(1)
            page.evaluate("""
                () => {
                    const all = [...document.querySelectorAll('button, a, span')];
                    const dl = all.find(el => el.innerText && el.innerText.trim() === 'Download alles als PDF');
                    if (dl) { dl.click(); }
                }
            """)
            # Wait for potential PDF response to be intercepted
            print("  Waiting for PDF response (up to 90s) ...")
            for _ in range(90):
                if pdf_bytes:
                    break
                time.sleep(1)

            if pdf_bytes:
                with open(OUT_FILE, "wb") as f:
                    f.write(pdf_bytes[-1])
                print(f"Saved server-generated PDF -> {OUT_FILE}")
                downloaded = True
        except Exception as e:
            print(f"JS trigger failed: {e}")

        # Attempt 2: try expect_download
        if not downloaded:
            try:
                print("Trying expect_download ...")
                import shutil
                with page.expect_download(timeout=30_000) as dl_info:
                    page.evaluate("""
                        () => {
                            const all = [...document.querySelectorAll('button, a, span')];
                            const dl = all.find(el => el.innerText && el.innerText.trim() === 'Download alles als PDF');
                            if (dl) { dl.click(); }
                        }
                    """)
                download = dl_info.value
                tmp = download.path()
                shutil.copy(tmp, OUT_FILE)
                print(f"Downloaded via expect_download -> {OUT_FILE}")
                downloaded = True
            except Exception as e:
                print(f"expect_download failed ({type(e).__name__})")

        # Fallback: Playwright PDF renderer
        if not downloaded:
            print("Falling back to Playwright PDF renderer ...")
            page.pdf(
                path=OUT_FILE,
                format="A4",
                print_background=True,
                margin={"top": "15mm", "bottom": "15mm", "left": "15mm", "right": "15mm"},
            )
            print(f"Saved (rendered) -> {OUT_FILE}")

        browser.close()

    size_kb = os.path.getsize(OUT_FILE) // 1024
    print(f"Done. File size: {size_kb} KB")


if __name__ == "__main__":
    main()

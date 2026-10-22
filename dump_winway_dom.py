"""
dump_winway_dom.py
Connects to your open Chrome via CDP and dumps the current page DOM
so we can find the real ticket selectors on winway.lk
"""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page    = context.pages[0]

    print(f"Current URL: {page.url}")
    print(f"Page title : {page.title()}")
    print()

    # Save full rendered DOM
    html = page.content()
    with open("winway_dom_dump.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"DOM saved to winway_dom_dump.html ({len(html):,} bytes)")
    print()

    # Print all buttons text
    buttons = page.query_selector_all("button")
    print(f"=== {len(buttons)} Buttons on page ===")
    for i, btn in enumerate(buttons[:30]):
        txt = (btn.inner_text() or "").strip().encode("ascii","replace").decode()
        cls = (btn.get_attribute("class") or "")[:60]
        print(f"  [{i:02d}] text={repr(txt):<25} class={cls}")

    print()

    # Print all links
    links = page.query_selector_all("a")
    print(f"=== {len(links)} Links on page ===")
    for i, a in enumerate(links[:30]):
        txt  = (a.inner_text() or "").strip().encode("ascii","replace").decode()
        href = (a.get_attribute("href") or "")[:80]
        print(f"  [{i:02d}] text={repr(txt):<20} href={href}")

    print()

    # Look for any element containing ticket-like alphanumeric serials
    print("=== Potential ticket number elements ===")
    for el in page.query_selector_all("*"):
        try:
            txt = (el.inner_text() or "").strip()
            import re
            # Ticket serial pattern: letter + 6 digits  e.g. T434194
            if re.match(r"^[A-Z]\d{6}$", txt):
                tag = el.evaluate("e => e.tagName")
                cls = el.get_attribute("class") or ""
                pid = el.get_attribute("data-testid") or ""
                print(f"  TICKET: {txt} | tag={tag} class={cls[:50]} testid={pid}")
        except Exception:
            continue

    # Print page structure summary
    print()
    print("=== Top-level divs in #root ===")
    root_children = page.query_selector_all("#root > *")
    for el in root_children[:10]:
        tag = el.evaluate("e => e.tagName")
        cls = (el.get_attribute("class") or "")[:80]
        txt_preview = (el.inner_text() or "")[:60].encode("ascii","replace").decode()
        print(f"  <{tag} class={cls}> -> {repr(txt_preview)}")

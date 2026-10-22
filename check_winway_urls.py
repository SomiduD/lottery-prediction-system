"""
Check what URLs winway.lk actually uses for ticket pages.
Clicks through the app to find the correct navigation path.
"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page    = context.pages[0]

    print(f"Current URL: {page.url}")
    print(f"Title: {page.title()}")
    print()

    # Try to find Mahajana Sampatha link/button in the nav
    print("=== Looking for Mahajana Sampatha nav items ===")
    for el in page.query_selector_all("button, a, li"):
        try:
            txt = (el.inner_text() or '').strip()
            if 'mahajana' in txt.lower() or 'sampatha' in txt.lower():
                tag  = el.evaluate("e => e.tagName")
                cls  = (el.get_attribute("class") or "")[:60]
                href = el.get_attribute("href") or ""
                print(f"  <{tag}> text='{txt[:40]}' href='{href}' class='{cls}'")
        except Exception:
            continue

    print()
    print("=== All nav/sidebar lottery buttons ===")
    # Look in the lottery carousel from the earlier DOM
    for el in page.query_selector_all("button.flex-shrink-0, button.flex.flex-col"):
        try:
            txt = (el.inner_text() or '').strip().encode('ascii','replace').decode()
            cls = (el.get_attribute("class") or "")[:80]
            if txt:
                print(f"  '{txt[:30]}' class='{cls}'")
        except Exception:
            continue

    print()
    # Check all hrefs to understand routing
    print("=== All page URLs/hrefs ===")
    seen = set()
    for a in page.query_selector_all("a[href]"):
        href = a.get_attribute("href") or ""
        if href not in seen and href.startswith("/"):
            seen.add(href)
            txt = (a.inner_text() or '').strip().encode('ascii','replace').decode()[:20]
            print(f"  href='{href}' text='{txt}'")

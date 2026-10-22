"""
Quick live test of the ticket row selectors against the current Chrome page.
"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright

SELECTORS = [
    ("data-testid", "[data-testid='ticket-card']"),
    ("flex-wrap XPath", "//div[contains(@class,'flex-wrap') and .//button[contains(@class,'from-primaryColor')]]"),
    ("flex-shrink-0 parent", "//div[contains(@class,'flex-shrink-0') and ./button[contains(@class,'from-primaryColor')]]/parent::div"),
    ("space-y-3 children", "//div[contains(@class,'space-y-3')]/div"),
    ("button.from-primaryColor", "button.from-primaryColor"),
    ("button XPath text=Add", "//button[normalize-space(text())='Add']"),
    ("button:has-text Add", "button:has-text('Add')"),
    ("Show Next 5", "button:has-text('Show Next 5 Tickets')"),
]

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page    = context.pages[0]

    print(f"URL: {page.url}")
    print()

    for name, sel in SELECTORS:
        try:
            if sel.startswith("//"):
                loc = page.locator(f"xpath={sel}")
            else:
                loc = page.locator(sel)
            count = loc.count()
            if count > 0:
                # Show first element's text
                txt = (loc.first.inner_text() or '').strip().encode('ascii','replace').decode()[:60]
                print(f"  [FOUND {count:2d}] {name:30s} -> {repr(txt)}")
            else:
                print(f"  [     0] {name}")
        except Exception as e:
            print(f"  [ERROR ] {name}: {e}")

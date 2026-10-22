import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page    = context.pages[0]

    cart_btn = page.locator("button:has(svg.lucide-shopping-cart)").first
    cart_btn.click()
    page.wait_for_timeout(3000)
    
    html = page.content()
    with open("c:\\Lottery\\cart_modal_dump.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("Dumped DOM after clicking cart.")

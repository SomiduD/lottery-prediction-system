import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page    = context.pages[0]

    print(f"URL: {page.url}")

    # Method 1: Locate button containing shopping cart SVG
    try:
        cart_btn = page.locator("button:has(svg.lucide-shopping-cart)").first
        print("Trying to click Cart button...")
        cart_btn.click()
        page.wait_for_timeout(2000)
        print("Clicked successfully.")
    except Exception as e:
        print(f"Failed to click: {e}")

    # Check if cart modal is open by looking for 'Clear All Tickets' or 'Ticket Count'
    if page.locator("text='Ticket Count'").count() > 0 or page.locator("text='Clear All Tickets'").count() > 0:
        print("CART IS OPEN!")
    else:
        print("CART IS NOT OPEN.")
        
        # Method 2: Fallback JS click on any element with aria-label="cart" or similar
        print("Trying JS fallback...")
        success = page.evaluate("""() => {
            const allEls = document.querySelectorAll('button, a, div, span, svg');
            for (const el of allEls) {
                const label = (el.getAttribute('aria-label') || '').toLowerCase();
                if (label.includes('cart')) {
                    el.closest('button, a, div').click();
                    return true;
                }
            }
            // Try SVGs in header
            const headerSvgs = document.querySelectorAll('header svg');
            for (const svg of headerSvgs) {
                const btn = svg.closest('button, a');
                if (btn) {
                    btn.click();
                    return true;
                }
            }
            return false;
        }""")
        print(f"JS Fallback success: {success}")
        page.wait_for_timeout(1000)
        if page.locator("text='Ticket Count'").count() > 0 or page.locator("text='Clear All Tickets'").count() > 0:
            print("CART IS OPEN NOW!")
        else:
            print("STILL NOT OPEN.")

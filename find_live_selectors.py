"""
Find exact ticket card structure from the live winway.lk React page via CDP.
"""
import sys, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page    = context.pages[0]

    print(f"URL: {page.url}")

    # Find all Add buttons
    add_btns = page.query_selector_all("button.from-primaryColor")
    if not add_btns:
        add_btns = page.query_selector_all("button.bg-gradient-to-r")
    print(f"Found {len(add_btns)} 'Add' buttons with from-primaryColor")

    # For each Add button, walk up to find the ticket card and ticket number
    for i, btn in enumerate(add_btns[:5]):
        btn_txt = (btn.inner_text() or '').strip()
        print(f"\n--- Button {i}: '{btn_txt}' ---")

        # Walk up max 5 levels to find the card container
        el = btn
        for level in range(6):
            parent = el.evaluate_handle("e => e.parentElement")
            parent_el = parent.as_element()
            if not parent_el:
                break
            tag   = parent_el.evaluate("e => e.tagName")
            cls   = (parent_el.get_attribute("class") or "")[:80]
            inner = (parent_el.inner_text() or "").strip().encode('ascii','replace').decode()[:80]
            print(f"  L{level}: <{tag} class='{cls}'> -> {repr(inner)}")
            el = parent_el

    # Now find actual ticket serial numbers (1 letter + 6 digits)
    print("\n=== Searching for ticket serials in all elements ===")
    all_els = page.query_selector_all("span, p, div, h1, h2, h3, h4, h5, h6")
    found = 0
    for el in all_els:
        try:
            txt = (el.inner_text() or '').strip()
            if re.match(r'^[A-Z]\d{6}$', txt):
                tag = el.evaluate("e => e.tagName")
                cls = (el.get_attribute("class") or "")[:70]
                pid = (el.get_attribute("data-testid") or "")
                print(f"  SERIAL: {txt} | <{tag} class='{cls}' testid='{pid}'>")
                found += 1
        except Exception:
            continue
    if not found:
        print("  No serials found in span/p/div — may be in different element type")

        # Try getting ALL text nodes
        all_text = page.evaluate("""() => {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            const texts = [];
            let node;
            while (node = walker.nextNode()) {
                const t = node.textContent.trim();
                if (/^[A-Z]\\d{6}$/.test(t)) {
                    const p = node.parentElement;
                    texts.push({
                        text: t,
                        tag: p.tagName,
                        cls: p.className.substring(0,80)
                    });
                }
            }
            return texts;
        }""")
        print(f"\n  Text node search found {len(all_text)} serials:")
        for item in all_text:
            print(f"    {item['text']} | <{item['tag']} class='{item['cls']}'>")

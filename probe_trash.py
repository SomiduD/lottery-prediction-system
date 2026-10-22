import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://localhost:9222")
    # Find winway.lk page (not devtools)
    page = next(p for ctx in browser.contexts for p in ctx.pages if 'winway' in p.url)

    print(f"Current URL: {page.url}")

    result = page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
        return btns.map(b => {
            const svg = b.querySelector('svg');
            return {
                text: b.innerText.trim().substring(0, 40),
                svgClass: svg ? svg.getAttribute('class') : null,
                parentText: b.parentElement ? b.parentElement.innerText.trim().substring(0, 80) : ''
            };
        }).filter(b => 
            (b.svgClass && (b.svgClass.includes('trash') || b.svgClass.includes('delete'))) ||
            b.text.includes('Remove') || b.text === 'X' || b.text === '×'
        );
    }""")

    print(f"\nFound {len(result)} trash/remove buttons:\n")
    for r in result:
        print(f"  text='{r['text']}' | svgClass='{r['svgClass']}' | parent='{r['parentText'][:60]}'")

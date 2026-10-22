"""
winway_run.py  (v3 — Multi-lottery)
=====================================
Standalone Winway automation. Works for ALL NLB lotteries.

Usage:
  Double-click step2_run_agent.bat   ← shows lottery menu
  python winway_run.py               ← interactive menu
  python winway_run.py --lottery "Handahana"  ← direct launch
"""

import sys, time, re, random, argparse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

try:
    from predictor import score_tickets as _score_fn
    HAS_PREDICTOR = True
except ImportError:
    HAS_PREDICTOR = False

try:
    import sqlite3, pathlib
    DB_PATH = str(pathlib.Path(__file__).parent / "nlb_results.db")
    HAS_DB = True
except Exception:
    HAS_DB = False

# ── Lottery Catalogue ──────────────────────────────────────────────────────
# All NLB lotteries available on winway.lk, with their draw schedule.
# draw_time = local Sri Lanka time (IST = UTC+5:30)

LOTTERIES = {
    "1": {
        "name":       "Mahajana Sampatha",
        "draw_time":  "21:00",
        "draw_days":  "Daily",
        "price_lkr":  40,
    },
    "2": {
        "name":       "Handahana",
        "draw_time":  "18:00",
        "draw_days":  "Daily",
        "price_lkr":  40,
    },
    "3": {
        "name":       "Ada Sampatha",
        "draw_time":  "19:30",
        "draw_days":  "Daily",
        "price_lkr":  20,
    },
    "4": {
        "name":       "Govisetha",
        "draw_time":  "19:00",
        "draw_days":  "Daily",
        "price_lkr":  20,
    },
    "5": {
        "name":       "Mega Power",
        "draw_time":  "15:00",
        "draw_days":  "Wed/Sat",
        "price_lkr":  40,
    },
    "6": {
        "name":       "Dhana Nidhanaya",
        "draw_time":  "21:00",
        "draw_days":  "Weekly (Wed)",
        "price_lkr":  40,
    },
    "7": {
        "name":       "Jaya",
        "draw_time":  "20:00",
        "draw_days":  "Daily",
        "price_lkr":  20,
    },
    "8": {
        "name":       "Suba",
        "draw_time":  "20:30",
        "draw_days":  "Daily",
        "price_lkr":  20,
    },
}

# ── Config ─────────────────────────────────────────────────────────────────
CDP_URL      = "http://localhost:9222"
TARGET       = 55   # tickets to add
KEEP         = 2    # tickets to keep
SESSION_WAIT = 120  # seconds to wait for re-login

# ── Helpers ─────────────────────────────────────────────────────────────────
def ts():   return time.strftime("%H:%M:%S")
def p(msg): print(f"[{ts()}] {msg}", flush=True)
def banner(msg): print(f"\n{'='*60}\n  {msg}\n{'='*60}", flush=True)

def get_page(browser):
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if "winway" in pg.url:
                return pg
    return browser.contexts[0].pages[0]

def is_logged_in(page) -> bool:
    try:
        content = page.inner_text("body").lower()
        if any(x in content for x in ["my profile", "my lotteries", "logout", "sign out", "somidu"]):
            return True
        if any(x in content for x in ["sign in", "create account", "password"]):
            return False
        return True
    except Exception:
        return True

def wait_for_login(page):
    if is_logged_in(page):
        return
    print("\n" + "!"*60)
    print("  SESSION EXPIRED — Please log in to winway.lk in Chrome!")
    print(f"  Waiting up to {SESSION_WAIT}s for you to log in...")
    print("!"*60)
    for i in range(SESSION_WAIT):
        time.sleep(1)
        if is_logged_in(page):
            p("Session restored. Continuing...")
            time.sleep(2)
            return
        if i % 20 == 19:
            print(f"  Still waiting... ({SESSION_WAIT-i-1}s left)")
    print("  WARNING: Could not confirm login. Proceeding anyway.")

def show_menu() -> dict:
    """Show lottery selection menu and return chosen lottery config."""
    import datetime
    now  = datetime.datetime.now()
    dow  = now.strftime("%A")
    hour = now.hour

    print("\n" + "="*60)
    print("  WINWAY TICKET AUTOMATION  —  Select Lottery")
    print(f"  Today: {dow}  {now.strftime('%H:%M')}")
    print("="*60)
    print()
    print(f"  {'#':<3}  {'Lottery':<22}  {'Draw':<8}  {'Days':<14}  Price")
    print("  " + "-"*58)

    # Highlight upcoming draws
    for key, cfg in LOTTERIES.items():
        draw_h, draw_m = map(int, cfg["draw_time"].split(":"))
        mins_until = (draw_h*60 + draw_m) - (hour*60 + now.minute)
        status = ""
        if 0 < mins_until <= 120:
            status = f" ← {mins_until}min!"
        elif mins_until < 0:
            status = " (done)"
        print(f"  {key:<3}  {cfg['name']:<22}  {cfg['draw_time']:<8}  {cfg['draw_days']:<14}  Rs.{cfg['price_lkr']}{status}")

    print()
    while True:
        choice = input("  Enter number (1-8): ").strip()
        if choice in LOTTERIES:
            return LOTTERIES[choice]
        print("  Invalid choice. Enter 1-8.")

def navigate_to_lottery(page, lottery_name: str) -> bool:
    """Navigate to the ticket selection page for the given lottery."""
    banner(f"STEP 1: Navigating to {lottery_name}")
    wait_for_login(page)

    # Check if already on a ticket page for this lottery
    try:
        body = page.inner_text("body")
        if lottery_name in body and page.locator("xpath=//button[normalize-space(text())='Add']").count() > 0:
            p(f"Already on {lottery_name} ticket page.")
            return True
    except Exception:
        pass

    # Navigate to homepage first
    try:
        page.goto("https://winway.lk/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
    except Exception as e:
        p(f"Homepage navigation warning: {e}")

    wait_for_login(page)

    # Find and click the lottery's Quick Buy button via JS
    p(f"Finding '{lottery_name}' on page...")
    clicked = page.evaluate("""(lotteryName) => {
        // Search all text nodes for the lottery name
        const allEls = Array.from(document.querySelectorAll('h3, h4, p, span, div, button, a'));
        for (const el of allEls) {
            const txt = (el.innerText || el.textContent || '').trim();
            if (txt !== lotteryName && !txt.startsWith(lotteryName)) continue;

            // Walk up to find a card container with a Quick Buy button
            let container = el;
            for (let i = 0; i < 12; i++) {
                container = container.parentElement;
                if (!container) break;
                const btns = Array.from(container.querySelectorAll('button'));
                for (const btn of btns) {
                    const t = (btn.innerText || '').toLowerCase().trim();
                    if (t.includes('quick buy') || t === 'buy' || t.includes('buy tickets')) {
                        btn.scrollIntoView({block: 'center'});
                        btn.click();
                        return {ok: true, found: lotteryName};
                    }
                }
            }
        }
        return {ok: false};
    }""", lottery_name)

    if clicked.get("ok"):
        p(f"Clicked Quick Buy for {lottery_name}.")
        page.wait_for_timeout(3000)
    else:
        p(f"Could not auto-click. Please click Quick Buy for {lottery_name} manually.")
        print("\n" + "-"*60)
        print(f"  ACTION NEEDED: Click Quick Buy for {lottery_name} in Chrome.")
        print("  Waiting 90 seconds...")
        print("-"*60)

    for i in range(90):
        time.sleep(1)
        try:
            add_count = page.locator("xpath=//button[normalize-space(text())='Add']").count()
            if add_count > 0:
                p(f"Ticket page detected after {i+1}s.")
                return True
        except Exception:
            pass
        if i % 20 == 19:
            print(f"  Still waiting... ({90-i-1}s left)")

    return False

def add_tickets(page, lottery_name: str) -> list[str]:
    """Add TARGET tickets to cart. Returns list of ticket text strings."""
    banner(f"STEP 2: Adding {TARGET} tickets to cart ({lottery_name})")
    added = []
    start = time.time()
    batch = 1

    while len(added) < TARGET:
        if int(time.time()-start) > 600:
            p("10-minute limit reached.")
            break

        p(f"Batch {batch} | Cart: {len(added)}/{TARGET} | {int(time.time()-start)}s")

        # Find all 'Add' buttons
        add_btns = []
        try:
            all_btns = page.locator("xpath=//button[normalize-space(text())='Add']").all()
            add_btns = all_btns
        except Exception:
            pass

        if not add_btns:
            try:
                add_btns = page.locator("button.from-primaryColor").all()
                add_btns = [b for b in add_btns if b.inner_text().strip() == "Add"]
            except Exception:
                pass

        p(f"  Found {len(add_btns)} 'Add' button(s)")
        if not add_btns:
            p("  No Add buttons found — checking login...")
            wait_for_login(page)
            time.sleep(2)
            continue

        chosen_btn = random.choice(add_btns)

        # Read the ticket's number string from its card
        ticket_id = "UNKNOWN"
        try:
            card_text = page.evaluate("""(btn) => {
                let el = btn;
                for (let i = 0; i < 10; i++) {
                    el = el.parentElement;
                    if (!el) break;
                    const txt = (el.innerText || '').trim();
                    const lines = txt.split('\\n').map(l => l.trim()).filter(Boolean);
                    // A ticket card has 5-8 tokens (letter + numbers) plus an Add button
                    if (lines.length >= 5 && lines.length <= 15) return txt;
                }
                return '';
            }""", chosen_btn.element_handle())

            if card_text:
                parts = [x.strip() for x in card_text.split("\n") if x.strip()]
                parts = [x for x in parts if x.lower() not in ("add","added","adding","remove","✓")]
                if parts and re.match(r'^\d{1,2}$', parts[0]):  # remove row index
                    parts = parts[1:]
                ticket_id = " ".join(parts[:7])
        except Exception:
            pass

        try:
            chosen_btn.scroll_into_view_if_needed()
            chosen_btn.click()
            page.wait_for_timeout(1500)
            added.append(ticket_id)
            p(f"  Added #{len(added)}: {ticket_id}")
        except Exception as e:
            p(f"  Click failed: {e}")

        # Show Next 5 Tickets
        try:
            next_btn = page.locator("button:has-text('Show Next 5 Tickets')").first
            next_btn.wait_for(state="visible", timeout=4000)
            next_btn.click()
            page.wait_for_timeout(1200)
            batch += 1
        except Exception:
            pass

    return added

def score_tickets_list(lottery_name: str, tickets: list[str]) -> list[tuple[str, float]]:
    """Score tickets using ML predictor or random fallback."""
    if not HAS_PREDICTOR or not tickets:
        p("predictor.py not available — using random scores")
        return sorted([(t, random.uniform(0, 100)) for t in tickets], key=lambda x: -x[1])
    try:
        ranked = _score_fn(lottery_name, tickets)
        return ranked
    except Exception as e:
        p(f"Scoring warning: {e}. Using random.")
        return sorted([(t, random.uniform(0, 100)) for t in tickets], key=lambda x: -x[1])

def open_cart_modal(page) -> bool:
    """Open the cart modal. Returns True when Remove buttons are visible."""

    # --- Check if modal already open (don't click again or it toggles closed) ---
    try:
        already = sum(1 for b in page.locator("button:has(svg.lucide-trash2)").all()
                      if b.inner_text().strip() == "Remove")
        if already > 0:
            p(f"  Cart modal already open ({already} Remove buttons visible).")
            return True
        if page.locator("text='Clear All Tickets'").count() > 0:
            p("  Cart modal already open (Clear All Tickets visible).")
            return True
    except Exception:
        pass

    # --- Click the shopping cart icon ---
    try:
        btn = page.locator("button:has(svg.lucide-shopping-cart)").first
        btn.click()
        p("  Clicked cart icon. Waiting for modal...")
    except Exception as e:
        p(f"  Cart icon click failed: {e}")
        return False

    # --- Poll up to 10s for Remove buttons (55 tickets = slow render) ---
    for attempt in range(20):  # 20 × 500ms = 10s
        page.wait_for_timeout(500)
        try:
            # Check for individual Remove buttons
            remove_btns = [b for b in page.locator("button:has(svg.lucide-trash2)").all()
                           if b.inner_text().strip() == "Remove"]
            if remove_btns:
                p(f"  Cart modal open. Found {len(remove_btns)} Remove button(s).")
                return True

            # Also accept Clear All Tickets as confirmation
            if page.locator("text='Clear All Tickets'").count() > 0:
                p("  Cart modal open (Clear All Tickets detected).")
                return True

            # Fallback: any trash2 button at all
            any_trash = page.locator("button:has(svg.lucide-trash2)").count()
            if any_trash > 0 and attempt >= 4:
                p(f"  Cart modal open (fallback: {any_trash} trash2 button(s)).")
                return True
        except Exception:
            pass

    p("  Cart modal did not open after 10s.")
    return False

def remove_from_cart(page, tickets_to_remove: list[str]) -> int:
    """
    Remove tickets from cart using the red Remove buttons in the cart modal.
    CONFIRMED selector (live DOM probe 2026-10-01):
      button with text='Remove' AND svg class contains 'trash2'
      Row text format: "C\\n6\\n7\\n6\\n0\\n8\\n5\\nRemove"
    """
    banner(f"STEP 4: Removing {len(tickets_to_remove)} low-scoring tickets")
    wait_for_login(page)

    ok = open_cart_modal(page)
    if not ok:
        page.wait_for_timeout(1000)
        ok = open_cart_modal(page)
    if not ok:
        p("WARNING: Could not open cart modal. Remove tickets manually.")
        return 0

    removed_count = 0
    for i, ticket in enumerate(tickets_to_remove):
        target_nums   = re.findall(r'\d+', ticket)
        target_letter = re.findall(r'\b[A-Z]\b', ticket)

        p(f"Removing {i+1}/{len(tickets_to_remove)}: {ticket[:35]}")

        # CONFIRMED: page.evaluate() accepts a SINGLE arg (must pass dict)
        result = page.evaluate("""({targetNums, targetLetter}) => {
            // Find only genuine 'Remove' buttons (trash2 SVG + text='Remove')
            const removeBtns = Array.from(document.querySelectorAll('button')).filter(btn => {
                const svg     = btn.querySelector('svg');
                const svgOk   = svg && (svg.getAttribute('class') || '').includes('trash2');
                const textOk  = btn.innerText.trim() === 'Remove';
                return svgOk && textOk;
            });

            for (const btn of removeBtns) {
                let el = btn;
                for (let i = 0; i < 8; i++) {
                    el = el.parentElement;
                    if (!el) break;
                    const rowText = (el.innerText || '').trim();

                    // Numbers in the row (word-boundary to avoid partial matches)
                    const rowNums    = (rowText.match(/(?<![\\d])\\d+(?![\\d])/g) || []);
                    const rowLetters = (rowText.match(/\\b[A-Z]\\b/g) || []);

                    const numsMatch   = targetNums.length >= 2 &&
                                        targetNums.every(n => rowNums.includes(n));
                    const letterMatch = targetLetter.length === 0 ||
                                        rowLetters.some(l => targetLetter.includes(l));

                    if (numsMatch && letterMatch && rowText.length < 400) {
                        btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                        btn.click();
                        return {ok: true, row: rowText.substring(0, 70)};
                    }
                }
            }
            return {ok: false, count: removeBtns.length};
        }""", {"targetNums": target_nums, "targetLetter": target_letter})

        if result and result.get("ok"):
            p(f"  ✓ Removed. Row: {result.get('row','')[:50]}")
            removed_count += 1
            page.wait_for_timeout(1800)

            # Re-open modal if it auto-closed
            try:
                rem_visible = sum(1 for b in page.locator("button:has(svg.lucide-trash2)").all()
                                  if b.inner_text().strip() == "Remove")
                if rem_visible == 0 and removed_count < len(tickets_to_remove):
                    p("  Modal closed — re-opening...")
                    open_cart_modal(page)
                    page.wait_for_timeout(1000)
            except Exception:
                pass
        else:
            p(f"  ✗ Not found (found {result.get('count','?')} Remove btns total)")

    return removed_count

def log_to_db(lottery_name: str, added: list, kept: list, ranked: list):
    """Log all purchased tickets and their scores to the database."""
    if not HAS_DB:
        return
    try:
        import datetime
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()

        # Create table if it doesn't exist
        cur.execute("""CREATE TABLE IF NOT EXISTS purchased_tickets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_date TEXT NOT NULL,
            purchase_time TEXT,
            day_of_week   TEXT,
            lottery_name  TEXT NOT NULL,
            ticket_numbers TEXT NOT NULL,
            score         REAL,
            was_kept      INTEGER NOT NULL DEFAULT 0,
            draw_date     TEXT,
            won           INTEGER
        )""")

        # Migrate old schema: add missing columns safely
        existing_cols = {row[1] for row in cur.execute("PRAGMA table_info(purchased_tickets)").fetchall()}
        for col, col_type in [("purchase_time", "TEXT"), ("day_of_week", "TEXT")]:
            if col not in existing_cols:
                cur.execute(f"ALTER TABLE purchased_tickets ADD COLUMN {col} {col_type}")
                p(f"  DB migrated: added column '{col}'")

        now  = datetime.datetime.now()
        date = now.strftime("%Y-%m-%d")
        tme  = now.strftime("%H:%M")
        dow  = now.strftime("%A")
        score_map = {t: s for t, s in ranked}
        for ticket in added:
            was_kept = 1 if ticket in kept else 0
            cur.execute("""INSERT INTO purchased_tickets
                          (purchase_date, purchase_time, day_of_week, lottery_name, ticket_numbers, score, was_kept)
                          VALUES (?, ?, ?, ?, ?, ?, ?)""",
                       (date, tme, dow, lottery_name, ticket, score_map.get(ticket, 0.0), was_kept))
        con.commit()
        con.close()
        p(f"Logged {len(added)} tickets to database (date={date}, day={dow})")
    except Exception as e:
        p(f"DB log warning: {e}")


# ── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Winway ticket automation")
    parser.add_argument("--lottery", help="Lottery name (skip menu)", default=None)
    parser.add_argument("--target",  type=int, default=TARGET, help=f"Tickets to add (default {TARGET})")
    parser.add_argument("--keep",    type=int, default=KEEP,   help=f"Tickets to keep (default {KEEP})")
    args = parser.parse_args()

    target = args.target
    keep   = args.keep

    print("\n" + "="*60)
    print("  WINWAY LOTTERY AUTOMATION  v3")
    print("="*60)

    # Choose lottery
    if args.lottery:
        lottery_cfg = next((v for v in LOTTERIES.values() if v["name"].lower() == args.lottery.lower()), None)
        if not lottery_cfg:
            lottery_cfg = {"name": args.lottery, "draw_time": "?", "draw_days": "?", "price_lkr": "?"}
    else:
        lottery_cfg = show_menu()

    lottery_name = lottery_cfg["name"]
    print(f"\n  Lottery   : {lottery_name}")
    print(f"  Draw time : {lottery_cfg['draw_time']} ({lottery_cfg['draw_days']})")
    print(f"  Strategy  : Add {target}, Keep best {keep}, Remove {target-keep}")
    print()

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            print(f"\nERROR: Cannot connect to Chrome at {CDP_URL}")
            print("Run step1_launch_chrome.bat first!\n")
            input("Press Enter to exit...")
            return

        page = get_page(browser)
        p(f"Connected to Chrome. URL: {page.url}")

        # Phase 1: Navigate
        if not navigate_to_lottery(page, lottery_name):
            print("\nERROR: Could not reach ticket page.")
            input("Press Enter to exit...")
            return

        # Phase 2: Add tickets
        added = add_tickets(page, lottery_name)
        if not added:
            print("\nERROR: No tickets added.")
            input("Press Enter to exit...")
            return
        p(f"Phase 2 done: Added {len(added)} tickets.")

        # Phase 3: Score
        banner("STEP 3: ML Scoring (day-of-week + recent trend + all-time frequency)")
        ranked   = score_tickets_list(lottery_name, added)
        to_keep  = [t for t, _ in ranked[:keep]]
        to_remove = [t for t, _ in ranked[keep:]]

        print()
        print(f"  {'Rank':<5} {'Score':>7}  {'Action':<14}  Ticket")
        print("  " + "-"*65)
        for i, (ticket, score) in enumerate(ranked, 1):
            action = ">>> KEEP <<<" if i <= keep else "remove"
            mark   = "✓ " if i <= keep else "  "
            print(f"  {i:<5} {score:>7.2f}  {action:<14}  {mark}{ticket[:40]}")
        print()

        # Phase 4: Remove
        removed = remove_from_cart(page, to_remove)

        # Phase 5: Log
        log_to_db(lottery_name, added, to_keep, ranked)

        # Final result
        print("\n" + "="*60)
        print(f"  DONE! {lottery_name.upper()} — BUY THESE {keep} TICKETS:")
        print("="*60)
        for i, (ticket, score) in enumerate(ranked[:keep], 1):
            print(f"\n  TICKET {i}: {ticket}")
            print(f"  Score   : {score:.2f}/100")
        print()

        if removed < len(to_remove):
            remaining = len(to_remove) - removed
            print(f"  ⚠  {remaining} tickets could not be auto-removed.")
            print("  Please remove them manually in Chrome before checkout.")
        else:
            print(f"  ✓ Cart has exactly {keep} ticket(s) — ready for payment!")

        print("="*60 + "\n")
        input("Press Enter to exit when payment is done...")

if __name__ == "__main__":
    main()

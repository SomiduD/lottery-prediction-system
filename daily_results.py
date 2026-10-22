"""
daily_results.py — NLB Daily Results Collector & Comparator
=============================================================
Scrapes today's winning numbers from nlb.lk and saves them
to the database. Then compares against your purchased tickets.

Real DB schema (confirmed 2026-10-03):
  lottery_results:    id, lottery_name, draw_number, draw_date, winning_raw, created_at
  individual_numbers: id, result_id, lottery_name, token
  purchased_tickets:  id, purchase_date, lottery_name, ticket_numbers,
                      score, was_kept, draw_date, won, purchase_time, day_of_week

Run:  python daily_results.py
  or: step3_daily_results.bat
"""

import sys, time, sqlite3, pathlib, re
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = pathlib.Path(__file__).parent / "nlb_results.db"
TODAY   = time.strftime("%Y-%m-%d")

def ts():  return time.strftime("%H:%M:%S")
def p(msg): print(f"[{ts()}] {msg}", flush=True)

# ── Lottery configs & URLs ───────────────────────────────────────────────────

# Known NLB result page URLs — tried in order until one works
LOTTERIES = {
    "Mahajana Sampatha": [
        "https://www.nlb.lk/results/mahajana-sampatha",
        "https://www.nlb.lk/lottery-results/mahajana-sampatha",
    ],
    "Handahana": [
        "https://www.nlb.lk/results/handahana",
        "https://www.nlb.lk/results/hadahana",
        "https://www.nlb.lk/lottery-results/handahana",
    ],
    "Ada Sampatha": [
        "https://www.nlb.lk/results/ada-sampatha",
        "https://www.nlb.lk/lottery-results/ada-sampatha",
    ],
    "Govisetha": [
        "https://www.nlb.lk/results/govisetha",
        "https://www.nlb.lk/lottery-results/govisetha",
    ],
    "Mega Power": [
        "https://www.nlb.lk/results/mega-power",
        "https://www.nlb.lk/lottery-results/mega-power",
    ],
    "Dhana Nidhanaya": [
        "https://www.nlb.lk/results/dhana-nidhanaya",
        "https://www.nlb.lk/lottery-results/dhana-nidhanaya",
    ],
    "Jaya": [
        "https://www.nlb.lk/results/jaya",
        "https://www.nlb.lk/lottery-results/jaya",
    ],
}

# ── Database ─────────────────────────────────────────────────────────────────

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Create daily_results table that matches the REAL schema
    # (We store in a separate table to avoid conflicts with the scraper's table)
    cur.execute("""CREATE TABLE IF NOT EXISTS daily_results (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        draw_date    TEXT NOT NULL,
        lottery_name TEXT NOT NULL,
        draw_number  TEXT,
        winning_raw  TEXT NOT NULL,
        scraped_at   TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(draw_date, lottery_name)
    )""")

    # Migrate purchased_tickets if columns missing
    existing = {r[1] for r in cur.execute("PRAGMA table_info(purchased_tickets)").fetchall()}
    for col, ctype in [("purchase_time","TEXT"),("day_of_week","TEXT"),("draw_date","TEXT"),("won","INTEGER")]:
        if col not in existing:
            cur.execute(f"ALTER TABLE purchased_tickets ADD COLUMN {col} {ctype}")

    con.commit()
    con.close()
    p("Database ready.")

# ── Scraping ─────────────────────────────────────────────────────────────────

def try_scrape_page(page, urls: list[str], lottery_name: str) -> dict | None:
    """Try each URL until we get valid winning numbers."""
    for url in urls:
        try:
            p(f"  Trying {url}...")
            resp = page.goto(url, wait_until="domcontentloaded", timeout=18000)
            if resp and resp.status >= 400:
                p(f"  HTTP {resp.status} — skipping")
                continue
            page.wait_for_timeout(2000)
            content = page.inner_text("body")
        except Exception as e:
            p(f"  Error: {str(e)[:80]}")
            continue

        # ── Try to find winning numbers ──────────────────────────────────
        # NLB format: letter (A-Z) + 6 single or double digit numbers
        # e.g. "T 4 3 4 1 9 4"  or  "H 12 05 44 22 08 33"

        # Pattern 1: letter + 6 numbers separated by spaces/newlines
        m = re.search(
            r'\b([A-Z])\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\b',
            content
        )
        if m:
            winning = " ".join(m.groups())
            draw_match = re.search(r'(?:draw|no\.?|#)\s*[:#]?\s*(\d{3,5})', content, re.IGNORECASE)
            draw_num = draw_match.group(1) if draw_match else ""
            p(f"  ✓ Draw #{draw_num}: {winning}")
            return {"lottery": lottery_name, "draw_number": draw_num, "winning_raw": winning}

        # Pattern 2: 4 numbers (Handahana-style: no letter, 4 numbers 1-70)
        m2 = re.search(
            r'\b(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\b',
            content
        )
        if m2:
            nums = list(m2.groups())
            # Sanity check: all 1-70
            if all(1 <= int(n) <= 70 for n in nums):
                winning = " ".join(nums)
                draw_match = re.search(r'(?:draw|no\.?|#)\s*[:#]?\s*(\d{3,5})', content, re.IGNORECASE)
                draw_num = draw_match.group(1) if draw_match else ""
                p(f"  ✓ Draw #{draw_num}: {winning} (4-number format)")
                return {"lottery": lottery_name, "draw_number": draw_num, "winning_raw": winning}

        p(f"  Could not parse winning numbers from {url}")

    return None


def scrape_all_results() -> list[dict]:
    from playwright.sync_api import sync_playwright
    results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx     = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = ctx.new_page()

        for lottery_name, urls in LOTTERIES.items():
            p(f"Scraping {lottery_name}...")
            result = try_scrape_page(page, urls, lottery_name)
            if result:
                results.append(result)
            else:
                p(f"  ✗ No results found for {lottery_name}")

        browser.close()

    return results


def save_results(results: list[dict]) -> int:
    """Save to daily_results table (uses winning_raw column)."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    saved = 0
    for r in results:
        try:
            cur.execute("""INSERT OR REPLACE INTO daily_results
                          (draw_date, lottery_name, draw_number, winning_raw)
                          VALUES (?, ?, ?, ?)""",
                       (TODAY, r["lottery"], r["draw_number"], r["winning_raw"]))
            saved += 1
        except Exception as e:
            p(f"  Save error: {e}")
    con.commit()
    con.close()
    return saved

# ── Performance comparison ───────────────────────────────────────────────────

def check_performance():
    """Compare today's winning numbers against your purchased tickets."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Get today's results from daily_results table
    cur.execute("""SELECT lottery_name, winning_raw
                   FROM daily_results WHERE draw_date=?""", (TODAY,))
    today_wins = {row[0]: row[1] for row in cur.fetchall()}

    # Also check lottery_results table for any results scraped by the main scraper today
    try:
        cur.execute("""SELECT lottery_name, winning_raw
                       FROM lottery_results WHERE draw_date=?""", (TODAY,))
        for row in cur.fetchall():
            if row[0] not in today_wins:
                today_wins[row[0]] = row[1]
    except Exception:
        pass

    if not today_wins:
        p("No winning numbers for today yet. Results may not be published yet.")
        con.close()
        return

    # Get today's purchased tickets
    cur.execute("""SELECT ticket_numbers, score, was_kept, lottery_name
                   FROM purchased_tickets WHERE purchase_date=?""", (TODAY,))
    tickets = cur.fetchall()

    if not tickets:
        p("No purchased tickets found for today.")
        con.close()
        return

    print("\n" + "="*65)
    print("  TODAY'S PERFORMANCE REPORT")
    print(f"  Date: {TODAY}")
    print("="*65)

    for lottery, winning in today_wins.items():
        print(f"\n  Lottery : {lottery}")
        print(f"  Winning : {winning}")
        w_nums   = set(re.findall(r'\b\d+\b', winning))
        w_letter = set(re.findall(r'\b[A-Z]\b', winning))

        lot_tickets = [(t, s, k) for t, s, k, l in tickets if l == lottery]
        if not lot_tickets:
            print("  No tickets purchased for this lottery today.")
            continue

        print(f"\n  {'Status':<8} {'Score':>7}  Ticket                         Match")
        print("  " + "-"*65)
        for ticket_str, score, was_kept in lot_tickets:
            t_nums   = set(re.findall(r'\b\d+\b', ticket_str))
            t_letter = set(re.findall(r'\b[A-Z]\b', ticket_str))
            matches  = len(t_nums & w_nums)
            l_match  = bool(t_letter & w_letter)

            status = "KEPT  " if was_kept else "removed"
            marker = ""
            if matches >= 6 and l_match: marker = " 🏆 JACKPOT!"
            elif matches >= 4:           marker = f" ★ {matches} numbers!"
            elif matches >= 3:           marker = f" ◆ {matches} numbers"

            print(f"  {status:<8} {(score or 0):>7.2f}  {ticket_str:<30} {matches}match{marker}")

            # Update won flag
            won = 1 if (matches >= 6 and l_match) else 0
            try:
                cur.execute("""UPDATE purchased_tickets SET draw_date=?, won=?
                               WHERE ticket_numbers=? AND purchase_date=?""",
                           (TODAY, won, ticket_str, TODAY))
            except Exception:
                pass

    con.commit()
    con.close()
    print()

# ── Manual entry fallback ────────────────────────────────────────────────────

def manual_entry():
    """Allow user to manually enter today's winning numbers."""
    print("\n" + "─"*50)
    print("  MANUAL RESULT ENTRY")
    print("  (use this if auto-scraping failed)")
    print("─"*50)

    lottery_list = list(LOTTERIES.keys())
    for i, name in enumerate(lottery_list, 1):
        print(f"  {i}. {name}")

    try:
        choice = input("\n  Enter lottery number (or Enter to skip): ").strip()
        if not choice:
            return
        lottery = lottery_list[int(choice)-1]
        winning = input(f"  Enter winning numbers for {lottery} (e.g. T 4 3 4 1 9 4): ").strip().upper()
        if winning:
            draw_num = input("  Enter draw number (or Enter to skip): ").strip()
            con = sqlite3.connect(DB_PATH)
            con.execute("""INSERT OR REPLACE INTO daily_results
                          (draw_date, lottery_name, draw_number, winning_raw)
                          VALUES (?, ?, ?, ?)""",
                       (TODAY, lottery, draw_num, winning))
            con.commit()
            con.close()
            p(f"Saved: {lottery} — {winning}")
    except Exception as e:
        p(f"Entry error: {e}")

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*60)
    print("  NLB DAILY RESULTS COLLECTOR")
    print(f"  Date: {TODAY}  ({datetime.now().strftime('%A')})")
    print("="*60 + "\n")

    init_db()

    # Auto-scrape
    p("Auto-scraping NLB results...")
    results = scrape_all_results()

    if results:
        saved = save_results(results)
        p(f"Saved {saved} result(s) to database.")
    else:
        p("Auto-scrape found nothing. Trying manual entry...")
        manual_entry()

    # Compare against purchased tickets
    print("\n" + "-"*40)
    p("Comparing with your purchased tickets...")
    check_performance()

    # Show what was saved
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("""SELECT lottery_name, draw_number, winning_raw
                          FROM daily_results WHERE draw_date=?""", (TODAY,)).fetchall()
    con.close()

    if rows:
        print("\n" + "="*60)
        print(f"  TODAY'S RESULTS IN DATABASE  ({TODAY})")
        print("="*60)
        for lottery, draw_num, winning in rows:
            print(f"  {lottery:<22}  #{draw_num:<8}  {winning}")
        print()

    input("Press Enter to exit...")

if __name__ == "__main__":
    main()

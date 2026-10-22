"""
performance_tracker.py — Prediction Performance Dashboard
===========================================================
Shows full stats on your lottery ticket predictions vs actual results.
Run anytime:  python performance_tracker.py
"""

import sys, sqlite3, pathlib, re, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = str(pathlib.Path(__file__).parent / "nlb_results.db")

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS purchased_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        purchase_date TEXT NOT NULL, lottery_name TEXT NOT NULL,
        ticket_numbers TEXT NOT NULL, score REAL, was_kept INTEGER NOT NULL DEFAULT 0,
        draw_date TEXT, won INTEGER
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS lottery_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT, draw_date TEXT NOT NULL,
        lottery_name TEXT NOT NULL, draw_number TEXT, winning_numbers TEXT NOT NULL,
        scraped_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(draw_date, lottery_name)
    )""")
    con.commit()
    return con

def sep(char="─", width=65): print(char * width)
def header(msg): sep("═"); print(f"  {msg}"); sep("═")

def main():
    con = init_db()
    cur = con.cursor()

    header("LOTTERY PREDICTION PERFORMANCE TRACKER")
    print(f"  Database: {DB_PATH}")
    print(f"  Report time: {time.strftime('%Y-%m-%d %H:%M')}")
    sep()

    # ── Overview ──────────────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM purchased_tickets")
    total_tickets = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM purchased_tickets WHERE was_kept=1")
    total_kept = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM purchased_tickets WHERE was_kept=0")
    total_removed = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT purchase_date) FROM purchased_tickets")
    total_days = cur.fetchone()[0]

    print(f"\n  Total tickets processed : {total_tickets}")
    print(f"  Total tickets KEPT      : {total_kept}")
    print(f"  Total tickets removed   : {total_removed}")
    print(f"  Days active             : {total_days}")
    if total_tickets > 0:
        keep_rate = total_kept / total_tickets * 100
        print(f"  Keep rate               : {keep_rate:.1f}% (kept {KEEP} of {total_tickets//total_days if total_days else 34} per day)")

    KEEP = 2

    # ── Per-day history ───────────────────────────────────────────────────
    print()
    sep()
    print("  DAILY HISTORY")
    sep()

    cur.execute("""
        SELECT pt.purchase_date, pt.lottery_name, 
               COUNT(*) as total, SUM(pt.was_kept) as kept,
               lr.winning_numbers,
               GROUP_CONCAT(CASE WHEN pt.was_kept=1 THEN pt.ticket_numbers END, '|') as kept_tickets,
               GROUP_CONCAT(CASE WHEN pt.was_kept=0 THEN pt.ticket_numbers END, '|') as removed_tickets
        FROM purchased_tickets pt
        LEFT JOIN lottery_results lr 
            ON lr.draw_date=pt.purchase_date AND lr.lottery_name=pt.lottery_name
        GROUP BY pt.purchase_date, pt.lottery_name
        ORDER BY pt.purchase_date DESC
        LIMIT 30
    """)
    rows = cur.fetchall()

    if not rows:
        print("  No history yet. Run winway_run.py and daily_results.py first.")
    else:
        for date, lottery, total, kept, winning, kept_tickets, removed_tickets in rows:
            print(f"\n  {date} — {lottery}")
            print(f"    Tickets added: {total} | Kept: {kept} | Removed: {total-(kept or 0)}")

            if winning:
                w_nums = set(re.findall(r'\b\d+\b', winning))
                w_letter = set(re.findall(r'\b[A-Z]\b', winning))
                print(f"    Winning numbers: {winning}")

                if kept_tickets:
                    print(f"    KEPT tickets:")
                    for kt in kept_tickets.split("|"):
                        if not kt: continue
                        t_nums = set(re.findall(r'\b\d+\b', kt))
                        t_letter = set(re.findall(r'\b[A-Z]\b', kt))
                        matches = len(t_nums & w_nums)
                        letter_ok = bool(t_letter & w_letter)
                        marker = ""
                        if matches == 6 and letter_ok: marker = " 🏆 JACKPOT!"
                        elif matches >= 4: marker = f" ★ {matches} numbers matched!"
                        elif matches >= 3: marker = f" ◆ {matches} numbers matched"
                        print(f"      ✅ {kt[:35]}{marker}")

                if removed_tickets:
                    removed = [x for x in removed_tickets.split("|") if x]
                    matched_removed = []
                    for rt in removed:
                        t_nums = set(re.findall(r'\b\d+\b', rt))
                        t_letter = set(re.findall(r'\b[A-Z]\b', rt))
                        matches = len(t_nums & w_nums)
                        letter_ok = bool(t_letter & w_letter)
                        if matches >= 3:
                            matched_removed.append((rt, matches, letter_ok))
                    if matched_removed:
                        print(f"    ⚠ Removed tickets that matched {len(matched_removed[0][1])}+ numbers:")
                        for rt, m, l in matched_removed:
                            print(f"      ❌ {rt[:35]}  ({m} numbers matched)")
                    else:
                        print(f"    Removed tickets: none matched 3+ numbers ✓")
            else:
                print(f"    Results not yet available (run daily_results.py after draw)")

    # ── Score accuracy ─────────────────────────────────────────────────────
    sep()
    print("  SCORE ACCURACY ANALYSIS")
    sep()

    cur.execute("""
        SELECT pt.ticket_numbers, pt.score, pt.was_kept, lr.winning_numbers
        FROM purchased_tickets pt
        JOIN lottery_results lr 
            ON lr.draw_date=pt.purchase_date AND lr.lottery_name=pt.lottery_name
        WHERE lr.winning_numbers IS NOT NULL
    """)
    scored_rows = cur.fetchall()

    if not scored_rows:
        print("\n  Not enough data yet. Run daily_results.py after each draw.")
    else:
        high_score_matches = 0
        low_score_matches  = 0
        high_total = 0
        low_total  = 0

        for ticket, score, was_kept, winning in scored_rows:
            t_nums = set(re.findall(r'\b\d+\b', ticket))
            w_nums = set(re.findall(r'\b\d+\b', winning))
            m = len(t_nums & w_nums)

            if (score or 0) >= 70:
                high_total += 1
                if m >= 3: high_score_matches += 1
            else:
                low_total += 1
                if m >= 3: low_score_matches += 1

        if high_total and low_total:
            h_rate = high_score_matches / high_total * 100
            l_rate = low_score_matches  / low_total  * 100
            print(f"\n  High-score tickets (score≥70): {high_score_matches}/{high_total} matched 3+ numbers ({h_rate:.1f}%)")
            print(f"  Low-score tickets  (score<70): {low_score_matches}/{low_total} matched 3+ numbers ({l_rate:.1f}%)")
            if h_rate > l_rate:
                print(f"\n  ✅ The predictor is working! High-score tickets match {h_rate-l_rate:.1f}% more often.")
            else:
                print(f"\n  ⚠ More data needed for reliable conclusions.")

    # ── Hot & cold numbers ─────────────────────────────────────────────────
    sep()
    print("  HOT & COLD NUMBERS (Last 30 days)")
    sep()

    cur.execute("""SELECT winning_numbers FROM lottery_results 
                  WHERE lottery_name='Mahajana Sampatha'
                  ORDER BY draw_date DESC LIMIT 30""")
    recent = [row[0] for row in cur.fetchall()]

    if recent:
        from collections import Counter
        all_nums = []
        for w in recent:
            all_nums.extend(re.findall(r'\b\d+\b', w))
        counts = Counter(all_nums).most_common()
        top_hot  = [f"{n}({c})" for n,c in counts[:8]]
        top_cold = [f"{n}({c})" for n,c in counts[-8:]]
        print(f"\n  🔥 Hottest numbers : {', '.join(top_hot)}")
        print(f"  ❄  Coldest numbers : {', '.join(top_cold)}")
    else:
        print("\n  No lottery results in database yet. Run daily_results.py first.")

    sep("═")
    print("\n  TIP: Run 'python daily_results.py' every night after the draw")
    print("  to keep this analysis up to date.\n")

    con.close()
    input("Press Enter to exit...")

if __name__ == "__main__":
    main()

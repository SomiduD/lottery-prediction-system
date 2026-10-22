"""
analytics.py — Day/Time Pattern Intelligence Dashboard
=======================================================
Real DB schema (confirmed 2026-10-03):
  lottery_results:    id, lottery_name, draw_number, draw_date, winning_raw, created_at
  individual_numbers: id, result_id, lottery_name, token
  purchased_tickets:  id, purchase_date, lottery_name, ticket_numbers,
                      score, was_kept, draw_date, won, purchase_time, day_of_week
  daily_results:      id, draw_date, lottery_name, draw_number, winning_raw, scraped_at

Run:  python analytics.py  or  step4_analytics.bat
"""

import sys, sqlite3, pathlib, re, time
from collections import Counter, defaultdict
from datetime import datetime, date

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = pathlib.Path(__file__).parent / "nlb_results.db"
DAY_NAMES = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

LOTTERY_DRAW_TIMES = {
    "Mahajana Sampatha": "21:00",
    "Handahana":         "18:00",
    "Ada Sampatha":      "19:30",
    "Govisetha":         "19:00",
    "Mega Power":        "15:00",
    "Dhana Nidhanaya":   "21:00",
    "Jaya":              "20:00",
    "Suba":              "20:30",
}

def sep(char="─", w=65): print(char * w)
def header(msg): sep("═"); print(f"  {msg}"); sep("═")

def get_con():
    return sqlite3.connect(DB_PATH)

def init_tables():
    con = get_con()
    # Ensure daily_results table exists
    con.execute("""CREATE TABLE IF NOT EXISTS daily_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        draw_date TEXT, lottery_name TEXT, draw_number TEXT,
        winning_raw TEXT NOT NULL,
        scraped_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(draw_date, lottery_name)
    )""")
    con.commit()
    con.close()

def extract_tokens(winning_str: str) -> list[str]:
    return re.findall(r'\b([A-Z0-9]+)\b', str(winning_str).upper())

def get_draw_dow(draw_date: str) -> int | None:
    """Convert draw_date string to 0=Mon…6=Sun."""
    try:
        return datetime.strptime(draw_date[:10], "%Y-%m-%d").weekday()
    except Exception:
        return None

def get_all_results(con, lottery_name: str = None) -> list[tuple]:
    """
    Get all winning results from both lottery_results and daily_results tables.
    Returns list of (draw_date, winning_raw, lottery_name).
    """
    rows = []

    # From main lottery_results (scraped by nlb_database.py)
    try:
        if lottery_name:
            r = con.execute("""SELECT draw_date, winning_raw, lottery_name
                               FROM lottery_results WHERE lottery_name=?
                               ORDER BY draw_date DESC""", (lottery_name,)).fetchall()
        else:
            r = con.execute("""SELECT draw_date, winning_raw, lottery_name
                               FROM lottery_results
                               ORDER BY draw_date DESC""").fetchall()
        rows.extend(r)
    except Exception:
        pass

    # From daily_results (scraped by daily_results.py)
    try:
        if lottery_name:
            r = con.execute("""SELECT draw_date, winning_raw, lottery_name
                               FROM daily_results WHERE lottery_name=?
                               ORDER BY draw_date DESC""", (lottery_name,)).fetchall()
        else:
            r = con.execute("""SELECT draw_date, winning_raw, lottery_name
                               FROM daily_results
                               ORDER BY draw_date DESC""").fetchall()
        rows.extend(r)
    except Exception:
        pass

    return rows

# ── Section 1: Today's Recommendation ─────────────────────────────────────

def section_todays_recommendation():
    header("TODAY'S LOTTERY RECOMMENDATION")
    now  = datetime.now()
    dow  = now.weekday()
    hour = now.hour

    print(f"  Today: {DAY_NAMES[dow]}  {now.strftime('%H:%M')}\n")

    con = get_con()

    # Count how many draws per lottery on THIS day of week (from draw_date)
    all_results = get_all_results(con)
    by_lottery_dow = defaultdict(int)
    for draw_date, _, lottery in all_results:
        d = get_draw_dow(draw_date)
        if d == dow:
            by_lottery_dow[lottery] += 1

    # Count purchased tickets per lottery
    try:
        pt_counts = dict(con.execute(
            "SELECT lottery_name, COUNT(*) FROM purchased_tickets GROUP BY lottery_name"
        ).fetchall())
    except Exception:
        pt_counts = {}

    upcoming = []
    for lottery, draw_time in LOTTERY_DRAW_TIMES.items():
        dh, dm  = map(int, draw_time.split(":"))
        mins_until = (dh*60 + dm) - (hour*60 + now.minute)
        draw_count = by_lottery_dow.get(lottery, 0)
        upcoming.append((lottery, draw_time, mins_until, draw_count))

    upcoming.sort(key=lambda x: (x[2] < 0, abs(x[2])))

    print(f"  {'Lottery':<22}  {'Draw':<6}  {'Status':<16}  {'Historical Data'}")
    print("  " + "-"*62)
    for lottery, draw_time, mins_until, draw_count in upcoming:
        if mins_until > 0:
            status = f"in {mins_until}min" if mins_until <= 120 else f"in {mins_until//60}h{mins_until%60}m"
        else:
            status = "done today"
        data_note = f"{draw_count} draws on {DAY_NAMES[dow]}s" if draw_count else "no data yet"
        arrow = " ◀ BUY NOW!" if 0 < mins_until <= 60 else ""
        print(f"  {lottery:<22}  {draw_time:<6}  {status:<16}  {data_note}{arrow}")

    con.close()
    print()

# ── Section 2: Day-of-Week Hot Numbers ────────────────────────────────────

def section_day_of_week_patterns():
    header("HOT NUMBERS BY DAY OF WEEK")

    con = get_con()
    today_dow = date.today().weekday()

    # Get distinct lotteries from both tables
    lotteries = set()
    for tbl in ["lottery_results", "daily_results"]:
        try:
            for row in con.execute(f"SELECT DISTINCT lottery_name FROM {tbl}").fetchall():
                lotteries.add(row[0])
        except Exception:
            pass

    for lottery in sorted(lotteries):
        print(f"\n  📊 {lottery}")
        sep("·", 50)

        rows = get_all_results(con, lottery)
        if not rows:
            print("    No data yet.")
            continue

        by_dow = defaultdict(list)
        for draw_date, winning, _ in rows:
            dow = get_draw_dow(draw_date)
            if dow is not None:
                tokens = [t for t in extract_tokens(winning) if t.isdigit()]
                by_dow[dow].extend(tokens)

        for dow in range(7):
            if dow not in by_dow or not by_dow[dow]:
                continue
            counts = Counter(by_dow[dow])
            total  = sum(counts.values())
            top5   = counts.most_common(5)
            is_today = " ← TODAY" if dow == today_dow else ""
            nums_str = "  ".join(f"{n}({c/total*100:.0f}%)" for n, c in top5)
            print(f"    {DAY_NAMES[dow]:<11}: {nums_str}{is_today}")

    con.close()
    print()

# ── Section 3: Recent Number Trends ───────────────────────────────────────

def section_recent_trends():
    header("RECENT NUMBER TRENDS (Last 7 Draws Per Lottery)")

    con = get_con()

    # Get distinct lotteries
    lotteries = set()
    for tbl in ["lottery_results", "daily_results"]:
        try:
            for row in con.execute(f"SELECT DISTINCT lottery_name FROM {tbl}").fetchall():
                lotteries.add(row[0])
        except Exception:
            pass

    for lottery in sorted(lotteries):
        rows = get_all_results(con, lottery)
        if not rows:
            continue

        recent7 = rows[:7]  # Already ordered DESC
        all_tokens = []
        all_nums   = []
        for _, winning, _ in recent7:
            tks = extract_tokens(winning)
            all_tokens.extend(tks)
            all_nums.extend([int(t) for t in tks if t.isdigit()])

        if not all_tokens:
            continue

        counts  = Counter(all_tokens)
        hot5    = counts.most_common(5)
        avg_num = sum(all_nums)/len(all_nums) if all_nums else 0

        if avg_num > 45:
            trend = "HIGH numbers trending 📈"
        elif avg_num < 25:
            trend = "LOW numbers trending 📉"
        else:
            trend = "MIXED numbers ↔"

        print(f"\n  {lottery}")
        print(f"    Hot (last 7 draws): {', '.join(f'{t}×{c}' for t,c in hot5)}")
        print(f"    Avg number: {avg_num:.1f}  →  {trend}")
        print(f"    Dates: {', '.join(r[0] for r in recent7)}")

    con.close()
    print()

# ── Section 4: Prediction Accuracy ────────────────────────────────────────

def section_prediction_accuracy():
    header("YOUR PREDICTION ACCURACY")

    con = get_con()

    # Match purchased_tickets with daily_results (both use draw_date=purchase_date)
    rows = []
    try:
        rows = con.execute("""
            SELECT pt.purchase_date, pt.lottery_name,
                   pt.ticket_numbers, pt.score, pt.was_kept, pt.day_of_week,
                   dr.winning_raw
            FROM purchased_tickets pt
            JOIN daily_results dr
                ON dr.draw_date = pt.purchase_date
                AND dr.lottery_name = pt.lottery_name
            ORDER BY pt.purchase_date DESC
        """).fetchall()
    except Exception:
        pass

    # Also try matching with lottery_results
    try:
        rows2 = con.execute("""
            SELECT pt.purchase_date, pt.lottery_name,
                   pt.ticket_numbers, pt.score, pt.was_kept, pt.day_of_week,
                   lr.winning_raw
            FROM purchased_tickets pt
            JOIN lottery_results lr
                ON lr.draw_date = pt.purchase_date
                AND lr.lottery_name = pt.lottery_name
            ORDER BY pt.purchase_date DESC
        """).fetchall()
        # Merge, avoiding duplicates
        existing = {(r[0], r[2]) for r in rows}
        for r in rows2:
            if (r[0], r[2]) not in existing:
                rows.append(r)
    except Exception:
        pass

    if not rows:
        print("\n  No matched data yet.")
        print("  Run winway_run.py daily, then step3_daily_results.bat after each draw.")
        con.close()
        return

    by_lottery = defaultdict(lambda: {"kept_m":0,"removed_m":0,"kept_t":0,"removed_t":0})

    print(f"\n  {'Date':<12} {'Lottery':<20} {'Kept?':<7} {'Score':>7}  {'Match'}")
    print("  " + "-"*68)

    for date_s, lottery, ticket, score, was_kept, dow, winning in rows:
        t_nums = set(re.findall(r'\b\d+\b', ticket))
        w_nums = set(re.findall(r'\b\d+\b', str(winning)))
        matches = len(t_nums & w_nums)

        status = "KEPT  " if was_kept else "removed"
        mark   = f"★ {matches} match!" if matches >= 3 else f"{matches} match"
        print(f"  {date_s:<12} {lottery[:18]:<20} {status:<7} {(score or 0):>7.2f}  {mark}")

        if was_kept:
            by_lottery[lottery]["kept_t"] += 1
            if matches >= 3: by_lottery[lottery]["kept_m"] += 1
        else:
            by_lottery[lottery]["removed_t"] += 1
            if matches >= 3: by_lottery[lottery]["removed_m"] += 1

    print()
    sep()
    print("  PREDICTOR ACCURACY SUMMARY")
    sep()
    for lottery, s in by_lottery.items():
        kt, km = s["kept_t"], s["kept_m"]
        rt, rm = s["removed_t"], s["removed_m"]
        if kt and rt:
            k_rate = km/kt*100
            r_rate = rm/rt*100
            verdict = "✅ Predictor working!" if k_rate >= r_rate else "⚠ Need more data"
            print(f"\n  {lottery}")
            print(f"    Kept    matched 3+: {km}/{kt} ({k_rate:.0f}%)")
            print(f"    Removed matched 3+: {rm}/{rt} ({r_rate:.0f}%)")
            print(f"    {verdict}")

    con.close()
    print()

# ── Section 5: Number Range Analysis ──────────────────────────────────────

def section_number_range():
    header("HIGH vs LOW NUMBER BIAS BY DAY")

    con = get_con()
    lotteries = set()
    for tbl in ["lottery_results", "daily_results"]:
        try:
            for row in con.execute(f"SELECT DISTINCT lottery_name FROM {tbl}").fetchall():
                lotteries.add(row[0])
        except Exception:
            pass

    for lottery in sorted(lotteries):
        rows = get_all_results(con, lottery)
        if len(rows) < 5:
            continue

        high_days = defaultdict(int)
        low_days  = defaultdict(int)

        for draw_date, winning, _ in rows:
            nums = [int(t) for t in extract_tokens(winning) if t.isdigit()]
            if not nums: continue
            avg = sum(nums)/len(nums)
            dow = get_draw_dow(draw_date)
            if dow is None: continue
            if avg > 40:   high_days[dow] += 1
            elif avg < 25: low_days[dow]  += 1

        if not high_days and not low_days:
            continue

        print(f"\n  {lottery}")
        for dow in range(7):
            h = high_days.get(dow, 0)
            l = low_days.get(dow, 0)
            if h or l:
                bar  = "H"*h + "L"*l
                note = "HIGH bias" if h > l else "LOW bias" if l > h else "MIXED"
                print(f"    {DAY_NAMES[dow]:<11}: {bar:<12} ({note})")

    con.close()
    print()

# ── Section 6: Purchased Tickets Summary ──────────────────────────────────

def section_recent_purchases():
    header("RECENT PURCHASES (Last 7 Days)")

    con = get_con()
    try:
        rows = con.execute("""
            SELECT purchase_date, day_of_week, lottery_name,
                   COUNT(*) as total, SUM(was_kept) as kept
            FROM purchased_tickets
            GROUP BY purchase_date, lottery_name
            ORDER BY purchase_date DESC
            LIMIT 14
        """).fetchall()
    except Exception:
        rows = []

    if not rows:
        print("\n  No purchases yet. Run step2_run_agent.bat to start.")
        con.close()
        return

    print(f"\n  {'Date':<12} {'Day':<11} {'Lottery':<22}  Added  Kept  Removed")
    print("  " + "-"*66)
    for date_s, dow_str, lottery, total, kept in rows:
        kept   = kept or 0
        removed = total - kept
        print(f"  {date_s:<12} {(dow_str or '?'):<11} {lottery:<22}  {total:<6} {kept:<5} {removed}")

    con.close()
    print()

# ── Main ──────────────────────────────────────────────────────────────────

def main():
    init_tables()
    print("\n" + "="*65)
    print("  WINWAY INTELLIGENCE DASHBOARD")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*65)

    section_todays_recommendation()
    section_recent_purchases()
    section_recent_trends()
    section_day_of_week_patterns()
    section_number_range()
    section_prediction_accuracy()

    print("  TIP: Run step3_daily_results.bat each night after the draw.")
    print("  The more data collected, the better the ML scoring becomes.\n")
    input("Press Enter to exit...")

if __name__ == "__main__":
    main()

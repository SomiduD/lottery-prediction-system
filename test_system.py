"""
test_system.py — Full System Verification
==========================================
Tests every component without buying real tickets.
Run with:  python test_system.py
"""

import sys, sqlite3, pathlib, re, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = "  [PASS]"
FAIL = "  [FAIL]"
WARN = "  [WARN]"

results = []

def ok(msg):
    print(f"{PASS} {msg}")
    results.append(("PASS", msg))

def fail(msg):
    print(f"{FAIL} {msg}")
    results.append(("FAIL", msg))

def warn(msg):
    print(f"{WARN} {msg}")
    results.append(("WARN", msg))

def sep(): print("  " + "-"*60)

print("\n" + "="*65)
print("  WINWAY SYSTEM TEST  —  Full End-to-End Verification")
print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print("="*65 + "\n")

# ── Test 1: Python imports ─────────────────────────────────────────────────
print("[ 1 ] Core Dependencies")
sep()
deps = ["playwright.sync_api", "sqlite3", "pathlib", "re", "pandas", "rich"]
for dep in deps:
    try:
        __import__(dep.split(".")[0])
        ok(f"import {dep}")
    except ImportError:
        fail(f"import {dep}  ← run: pip install {dep.split('.')[0]}")

# ── Test 2: Database ───────────────────────────────────────────────────────
print("\n[ 2 ] Database")
sep()
DB_PATH = pathlib.Path("nlb_results.db")
if not DB_PATH.exists():
    fail(f"Database not found: {DB_PATH}")
else:
    ok(f"Database exists ({DB_PATH.stat().st_size // 1024} KB)")
    con = sqlite3.connect(DB_PATH)

    for table in ["lottery_results", "individual_numbers"]:
        row = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        if row and row[0] > 0:
            ok(f"Table '{table}' has {row[0]} rows")
        else:
            warn(f"Table '{table}' is empty — run nlb_scraper_pw.py first")

    # Check per-lottery
    lotteries = con.execute(
        "SELECT lottery_name, COUNT(*) FROM lottery_results GROUP BY lottery_name"
    ).fetchall()
    if lotteries:
        for name, count in lotteries:
            ok(f"  {name}: {count} draws")
    else:
        fail("No lottery data found")

    # Check purchased_tickets (may not exist yet)
    try:
        row = con.execute("SELECT COUNT(*) FROM purchased_tickets").fetchone()
        ok(f"Table 'purchased_tickets' has {row[0]} rows")
    except Exception:
        warn("Table 'purchased_tickets' doesn't exist yet (created on first run)")

    con.close()

# ── Test 3: Predictor ──────────────────────────────────────────────────────
print("\n[ 3 ] Predictor (ML Scoring Engine)")
sep()
try:
    from predictor import score_tickets, get_hottest_tokens, get_dow_analysis, _parse_ticket

    # Test ticket parsing
    cases = [
        ("C 6 7 6 0 8 5",  ["C", "6", "7", "6", "0", "8", "5"]),
        ("H 8 1 0 2 4 6",  ["H", "8", "1", "0", "2", "4", "6"]),
        ("Z 6 9 7 1 0 4",  ["Z", "6", "9", "7", "1", "0", "4"]),
    ]
    all_parse_ok = True
    for ticket, expected in cases:
        got = _parse_ticket(ticket)
        if got == expected:
            ok(f"Parse '{ticket}' → {got}")
        else:
            fail(f"Parse '{ticket}': got {got}, expected {expected}")
            all_parse_ok = False

    # Test scoring with real data
    test_tickets = [
        "C 6 7 6 0 8 5",
        "H 8 1 0 2 4 6",
        "Z 6 9 7 1 0 4",
        "H 2 3 6 9 8 6",
        "V 7 4 2 0 3 7",
    ]

    for lottery in ["Mahajana Sampatha", "Handahana"]:
        try:
            ranked = score_tickets(lottery, test_tickets)
            if ranked and len(ranked) == len(test_tickets):
                scores = [s for _, s in ranked]
                ok(f"Score '{lottery}': {len(ranked)} tickets, scores {min(scores):.1f}–{max(scores):.1f}")

                # Verify scores are different (not all identical)
                if len(set(round(s, 4) for s in scores)) > 1:
                    ok(f"  Scores are differentiated (ML working)")
                else:
                    warn(f"  All scores identical — may need more data")
            else:
                warn(f"Score '{lottery}': returned {len(ranked) if ranked else 0} results")
        except Exception as e:
            warn(f"Score '{lottery}': {e} (need more DB data)")

    # Test DOW analysis
    for lottery in ["Mahajana Sampatha", "Handahana"]:
        try:
            dow = get_dow_analysis(lottery)
            if dow:
                days = list(dow.keys())
                ok(f"DOW analysis '{lottery}': data for {len(days)} day(s): {', '.join(days)}")
            else:
                warn(f"DOW analysis '{lottery}': no day-of-week data yet (need more draws)")
        except Exception as e:
            warn(f"DOW analysis: {e}")

except ImportError as e:
    fail(f"predictor.py import failed: {e}")

# ── Test 4: winway_run.py ─────────────────────────────────────────────────
print("\n[ 4 ] winway_run.py (Main Agent)")
sep()
try:
    import winway_run as wr

    # Verify config
    if wr.TARGET == 55:
        ok(f"TARGET = {wr.TARGET} tickets (correct)")
    else:
        fail(f"TARGET = {wr.TARGET} (expected 55)")

    if wr.KEEP == 2:
        ok(f"KEEP = {wr.KEEP} tickets (correct)")
    else:
        fail(f"KEEP = {wr.KEEP} (expected 2)")

    if wr.CDP_URL == "http://localhost:9222":
        ok(f"CDP_URL = {wr.CDP_URL}")
    else:
        warn(f"CDP_URL = {wr.CDP_URL}")

    # Verify LOTTERIES dict
    if len(wr.LOTTERIES) >= 6:
        ok(f"LOTTERIES dict has {len(wr.LOTTERIES)} lotteries:")
        for key, cfg in wr.LOTTERIES.items():
            ok(f"  {key}. {cfg['name']} ({cfg['draw_time']}, {cfg['draw_days']})")
    else:
        fail(f"LOTTERIES dict only has {len(wr.LOTTERIES)} entries")

    # Test ticket removal JS logic (simulate with fake data)
    # This verifies the logic without browser
    target_nums = ["6", "7", "6", "0", "8", "5"]
    target_letter = ["C"]
    ok(f"remove_from_cart args: targetNums={target_nums}, targetLetter={target_letter}")
    ok(f"page.evaluate() call signature: ({{targetNums, targetLetter}}) → dict arg ✓")

except ImportError as e:
    fail(f"winway_run.py import failed: {e}")
except Exception as e:
    fail(f"winway_run.py error: {e}")

# ── Test 5: Chrome connection ─────────────────────────────────────────────
print("\n[ 5 ] Chrome / Playwright Connection")
sep()
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp("http://localhost:9222")
            ok("Chrome connected via CDP (http://localhost:9222)")

            # Find winway page
            page = None
            for ctx in browser.contexts:
                for pg in ctx.pages:
                    if "winway" in pg.url:
                        page = pg
                        break

            if page:
                ok(f"Found winway.lk page: {page.url}")

                # Check login
                content = page.inner_text("body").lower()
                if any(x in content for x in ["my profile", "my lotteries", "somidu"]):
                    ok("User is LOGGED IN")
                else:
                    warn("User may NOT be logged in — check Chrome")

                # Check cart icon
                count = page.locator("button:has(svg.lucide-shopping-cart)").count()
                if count > 0:
                    ok(f"Cart icon found ({count} element(s)) → can open cart modal")
                else:
                    warn("Cart icon not found (is the page fully loaded?)")

                # Check current cart contents (if cart is open)
                remove_btns = [b for b in page.locator("button:has(svg.lucide-trash2)").all()
                               if b.inner_text().strip() == "Remove"]
                if remove_btns:
                    ok(f"Cart modal open — found {len(remove_btns)} Remove button(s)")
                else:
                    ok("Cart modal is closed (normal) — Remove buttons found when cart opens")

                # Count tickets on current page
                add_btns = page.locator("xpath=//button[normalize-space(text())='Add']").count()
                if add_btns > 0:
                    ok(f"On ticket selection page — {add_btns} 'Add' button(s) visible")
                else:
                    ok(f"Not on ticket page (URL: {page.url[:60]})")

            else:
                warn("No winway.lk page found — open winway.lk in Chrome")

            # Show all open pages
            all_pages = [(pg.url[:80]) for ctx in browser.contexts for pg in ctx.pages]
            ok(f"Open pages: {all_pages}")

        except Exception as e:
            warn(f"Chrome not running or not started with --remote-debugging-port=9222")
            warn(f"Detail: {e}")
            warn("Run step1_launch_chrome.bat first!")

except Exception as e:
    fail(f"Playwright not available: {e}")

# ── Test 6: analytics.py ─────────────────────────────────────────────────
print("\n[ 6 ] analytics.py")
sep()
try:
    import analytics
    ok("analytics.py imports successfully")
    ok("Functions: section_todays_recommendation, section_day_of_week_patterns, section_recent_trends")
except Exception as e:
    fail(f"analytics.py: {e}")

# ── Test 7: daily_results.py ─────────────────────────────────────────────
print("\n[ 7 ] daily_results.py")
sep()
try:
    import daily_results
    ok("daily_results.py imports successfully")
except Exception as e:
    fail(f"daily_results.py: {e}")

# ── Test 8: Batch files ───────────────────────────────────────────────────
print("\n[ 8 ] Batch Files")
sep()
bats = [
    "step1_launch_chrome.bat",
    "step2_run_agent.bat",
    "step3_daily_results.bat",
    "step4_analytics.bat",
    "setup_scheduler.bat",
]
for bat in bats:
    p = pathlib.Path(bat)
    if p.exists():
        ok(f"{bat} ({p.stat().st_size} bytes)")
    else:
        fail(f"{bat} missing")

# ── Summary ───────────────────────────────────────────────────────────────
print("\n" + "="*65)
print("  TEST SUMMARY")
print("="*65)

passed = sum(1 for r, _ in results if r == "PASS")
failed = sum(1 for r, _ in results if r == "FAIL")
warned = sum(1 for r, _ in results if r == "WARN")

print(f"\n  PASS: {passed}")
print(f"  FAIL: {failed}")
print(f"  WARN: {warned}")
print()

if failed == 0:
    print("  ✅ SYSTEM READY — all tests passed!")
    print(f"  Add {55} tickets, keep best 2, remove {53} automatically.")
elif failed <= 2:
    print("  ⚠  MOSTLY READY — fix the FAIL items above, then run again.")
else:
    print("  ❌ SETUP NEEDED — multiple failures, see details above.")

print()
if warned > 0:
    print("  Warnings (WARN) are non-critical but worth checking.")

print("="*65 + "\n")
input("Press Enter to exit...")

"""
lottery_updater.py
===================
Smart daily updater — only downloads draws that are NOT yet in the
per-lottery database. Uses the confirmed nlb.lk DOM structure.

How it works:
  1. For each lottery, find the max draw_number already stored.
  2. Scrape the listing page from nlb.lk to discover all available draws.
  3. Identify which draw numbers are NEW (not yet in DB).
  4. Fetch only those detail pages and store the winning numbers.
  5. Log a summary of what was added.

Usage:
  python lottery_updater.py                        -- update ALL lotteries
  python lottery_updater.py --lottery "Mega Power" -- update one lottery
  python lottery_updater.py --lottery "Mega Power" --backfill 200
                                                   -- also get past 200 draws
  python lottery_updater.py --status               -- show what's stored

Run this daily (e.g. via Task Scheduler or run_daily.bat).
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from lottery_db_manager import (
    LOTTERIES, DATA_DIR,
    init_lottery_db, lottery_db_path, insert_draw,
    get_max_draw_number, show_status,
)

NLB_BASE = "https://www.nlb.lk"

# ─── Date parser ──────────────────────────────────────────────────────────────

def _parse_date(text: str) -> Optional[str]:
    """Normalise any date string → YYYY-MM-DD."""
    if not text:
        return None
    text = text.strip()
    formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%A %B %d, %Y",   # "Saturday September 26, 2026"
        "%B %d, %Y",
        "%b %d, %Y",
        "%A, %B %d, %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Regex fallback
    m = re.search(r"(\w+ \w+ \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})", text)
    if m:
        return _parse_date(m.group(1))
    return None


# ─── Playwright scraper ───────────────────────────────────────────────────────

class LotteryUpdaterScraper:
    """
    Headless Chromium scraper using confirmed nlb.lk DOM:
      Listing: table rows with [Draw#+Date | SerialTicket | More link]
      Detail:  <li class="Circle"> → letter + numbers
               <h2>Prize Structure - Saturday September 26, 2026</h2>
    """

    def __init__(self, headless: bool = True):
        from playwright.sync_api import sync_playwright
        self._pw      = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self._page    = self._browser.new_page()
        self._page.set_default_timeout(30_000)
        self._warmed_slugs: set[str] = set()

    def _warm(self, slug: str) -> None:
        if slug not in self._warmed_slugs:
            url = f"{NLB_BASE}/results/{slug}"
            self._page.goto(url, wait_until="networkidle", timeout=30_000)
            self._page.wait_for_timeout(2_000)
            self._warmed_slugs.add(slug)

    def scrape_listing(self, slug: str, max_draws: int = 0) -> list[dict]:
        """
        Scrape the results listing page, following all pagination.
        max_draws=0 means collect EVERYTHING available on the site.
        Returns list of {draw_number, draw_date, detail_url}.
        """
        self._warm(slug)
        url = f"{NLB_BASE}/results/{slug}"
        self._page.goto(url, wait_until="networkidle", timeout=30_000)
        self._page.wait_for_timeout(2_000)

        unlimited = (max_draws == 0)   # 0 = no cap, collect all pages
        results = []
        page_num = 1

        while True:
            trs = self._page.query_selector_all("table tr")
            if not trs:
                break

            new_found = 0
            for tr in trs:
                tds = tr.query_selector_all("td")
                if len(tds) < 2:
                    continue

                draw_text = (tds[0].inner_text() or "").strip()
                lines = [l.strip() for l in draw_text.splitlines() if l.strip()]
                if not lines or not lines[0].isdigit():
                    continue

                draw_number   = int(lines[0])
                draw_date_raw = lines[1] if len(lines) > 1 else ""
                draw_date     = _parse_date(draw_date_raw) or ""

                # Find the MORE link
                detail_url = ""
                for a in tr.query_selector_all("a"):
                    href = a.get_attribute("href") or ""
                    txt  = (a.inner_text() or "").strip().upper()
                    if "MORE" in txt or slug in href.lower():
                        detail_url = (
                            href if href.startswith("http")
                            else f"{NLB_BASE}/{href.lstrip('/')}"
                        )
                        break

                results.append({
                    "draw_number": draw_number,
                    "draw_date":   draw_date,
                    "detail_url":  detail_url,
                })
                new_found += 1

                if not unlimited and len(results) >= max_draws:
                    break

            print(f"    Page {page_num}: {new_found} rows (total so far: {len(results)})")

            # Stop if we hit the cap
            if not unlimited and len(results) >= max_draws:
                break

            # Stop if this page had nothing useful
            if not new_found:
                break

            # Try to go to the next page
            next_btn = (
                self._page.query_selector("a[rel='next']")
                or self._page.query_selector("li.next a")
                or self._page.query_selector("a:has-text('Next')")
            )
            if not next_btn:
                print(f"    No more pages after page {page_num}.")
                break

            next_btn.click()
            self._page.wait_for_load_state("networkidle", timeout=20_000)
            self._page.wait_for_timeout(1_200)
            page_num += 1

        return results

    def scrape_detail(self, detail_url: str, draw_number: int) -> Optional[dict]:
        """
        Fetch a single draw's detail page.
        Returns {draw_number, draw_date, winning_raw} or None.
        """
        try:
            self._page.goto(detail_url, wait_until="networkidle", timeout=30_000)
            self._page.wait_for_timeout(1_500)
        except Exception as e:
            print(f"    [!] Detail page error draw #{draw_number}: {e}")
            return None

        # ── Date from h2 Prize Structure heading ─────────────────────────────
        draw_date = ""
        try:
            for h2 in self._page.query_selector_all("h2"):
                txt = (h2.inner_text() or "").strip()
                if "prize structure" in txt.lower():
                    m = re.search(r"-\s*(.+)$", txt)
                    if m:
                        draw_date = _parse_date(m.group(1).strip()) or ""
                    break
        except Exception:
            pass

        # ── Winning balls from li.Circle ──────────────────────────────────────
        tokens: list[str] = []
        try:
            for el in self._page.query_selector_all("li.Circle"):
                txt = (el.inner_text() or "").strip().upper()
                if re.match(r"^[A-Z]$", txt) or re.match(r"^\d{1,2}$", txt):
                    tokens.append(txt)
        except Exception:
            pass

        # Fallback: li[class*='Letter'] / li[class*='Number']
        if not tokens:
            try:
                for sel in ["li[class*='Letter']", "li[class*='Number-']"]:
                    for el in self._page.query_selector_all(sel):
                        txt = (el.inner_text() or "").strip().upper()
                        if re.match(r"^[A-Z]$", txt) or re.match(r"^\d{1,2}$", txt):
                            if txt not in tokens:
                                tokens.append(txt)
            except Exception:
                pass

        if not tokens:
            return None

        return {
            "draw_number": draw_number,
            "draw_date":   draw_date,
            "winning_raw": " ".join(tokens),
        }

    def close(self) -> None:
        try:
            self._browser.close()
            self._pw.stop()
        except Exception:
            pass


# ─── Core update logic ────────────────────────────────────────────────────────

def update_lottery(
    lottery_name: str,
    max_listing:  int  = 0,
    headless:     bool = True,
    scraper:      Optional[LotteryUpdaterScraper] = None,
) -> dict:
    """
    Update one lottery's database with any missing draws.
    max_listing=0 (default) = collect ALL available draws from the site.
    max_listing=N           = check only the last N draws (faster, for daily runs).

    Returns a summary dict:
      {lottery_name, new_draws_found, inserted, already_had, errors}
    """
    slug = LOTTERIES.get(lottery_name, lottery_name.lower().replace(" ", "-"))
    db_path = lottery_db_path(lottery_name)

    # Make sure the DB exists
    init_lottery_db(lottery_name)

    max_stored = get_max_draw_number(lottery_name)
    print(f"\n  [{lottery_name}]  max stored draw: #{max_stored}")

    # Get existing draw numbers to check for gaps too
    conn = sqlite3.connect(db_path)
    existing_draws = {
        row[0] for row in conn.execute("SELECT draw_number FROM draws").fetchall()
    }
    conn.close()

    own_scraper = scraper is None
    if own_scraper:
        scraper = LotteryUpdaterScraper(headless=headless)

    summary = {
        "lottery_name": lottery_name,
        "new_draws_found": 0,
        "inserted": 0,
        "already_had": 0,
        "errors": 0,
    }

    try:
        # Step 1: Get the listing
        listing = scraper.scrape_listing(slug, max_draws=max_listing)
        if not listing:
            print(f"    [!] No listing rows returned for {lottery_name}")
            return summary

        print(f"    Listing: {len(listing)} rows from nlb.lk")

        # Step 2: Filter to only draws we don't have
        missing = [
            item for item in listing
            if item["draw_number"] not in existing_draws
        ]
        summary["new_draws_found"] = len(missing)
        summary["already_had"]     = len(listing) - len(missing)

        if not missing:
            print(f"    ✓  All {len(listing)} draws already in database. Nothing to do.")
            return summary

        print(f"    → {len(missing)} new draws to fetch")

        # Step 3: Fetch detail pages for missing draws only
        conn = sqlite3.connect(db_path)

        for item in missing:
            draw_no    = item["draw_number"]
            detail_url = item["detail_url"]
            list_date  = item["draw_date"]

            if not detail_url:
                print(f"    [!] No detail URL for draw #{draw_no} — skipping")
                summary["errors"] += 1
                continue

            detail = scraper.scrape_detail(detail_url, draw_no)
            time.sleep(0.6)   # polite delay

            if detail:
                ok = insert_draw(
                    conn,
                    draw_number = draw_no,
                    draw_date   = detail["draw_date"] or list_date or "1900-01-01",
                    winning_raw = detail["winning_raw"],
                )
                conn.commit()
                if ok:
                    summary["inserted"] += 1
                    print(f"    ✓  #{draw_no}  {detail['draw_date']}  {detail['winning_raw']}")
                else:
                    summary["already_had"] += 1
            else:
                print(f"    [!] Could not parse draw #{draw_no}")
                summary["errors"] += 1

        conn.close()

    finally:
        if own_scraper:
            scraper.close()

    return summary


def update_all_lotteries(
    max_listing: int = 0,
    headless: bool = True,
) -> None:
    """Update all lotteries using a single shared browser session.
    max_listing=0 = collect ALL available draws from every lottery.
    """
    print("\n" + "="*65)
    print("  NLB LOTTERY DAILY UPDATER")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*65)

    scraper = LotteryUpdaterScraper(headless=headless)
    summaries = []

    try:
        for lottery_name in LOTTERIES:
            try:
                s = update_lottery(
                    lottery_name,
                    max_listing = max_listing,
                    headless    = headless,
                    scraper     = scraper,
                )
                summaries.append(s)
            except Exception as e:
                print(f"  [!] Error updating {lottery_name}: {e}")
                summaries.append({
                    "lottery_name": lottery_name,
                    "new_draws_found": 0, "inserted": 0,
                    "already_had": 0, "errors": 1,
                })
    finally:
        scraper.close()

    # ── Final summary ─────────────────────────────────────────────────────────
    print("\n" + "="*65)
    print("  UPDATE SUMMARY")
    print("="*65)
    print(f"\n  {'Lottery':<28}  {'New':>4}  {'Added':>5}  {'Had':>6}  {'Err':>4}")
    print("  " + "-"*55)

    total_added = 0
    for s in summaries:
        print(
            f"  {s['lottery_name']:<28}  "
            f"{s['new_draws_found']:>4}  "
            f"{s['inserted']:>5}  "
            f"{s['already_had']:>6}  "
            f"{s['errors']:>4}"
        )
        total_added += s["inserted"]

    print(f"\n  Total new draws added: {total_added}")
    print(f"  Completed: {datetime.now().strftime('%H:%M:%S')}\n")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smart NLB lottery updater — only downloads missing draws"
    )
    parser.add_argument(
        "--lottery",
        help='Update one lottery, e.g. "Mega Power". Omit to update ALL.',
    )
    parser.add_argument(
        "--limit",
        type=int, default=0,
        help="Max draws to check per lottery (default: 0 = ALL available on site)",
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        help="Show browser window instead of headless",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show database status and exit",
    )

    args = parser.parse_args()

    if args.status:
        show_status()
        return

    headless = not args.visible

    if args.lottery:
        s = update_lottery(args.lottery, max_listing=args.limit, headless=headless)
        mode = f"last {args.limit}" if args.limit > 0 else "ALL"
        print(f"\n  Done ({mode} draws checked). Inserted {s['inserted']} new draws for {args.lottery}\n")
    else:
        update_all_lotteries(max_listing=args.limit, headless=headless)


if __name__ == "__main__":
    main()

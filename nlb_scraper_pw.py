"""
nlb_scraper_pw.py
==================
Standalone Playwright-based scraper for nlb.lk.
Bypasses the JS cookie challenge, reads the results table, and populates
the SQLite database used by nlb_database.py / predictor.py.

Structure discovered (2026-09-27):
  Listing page: https://www.nlb.lk/results/<slug>
    Table: 3 columns per row -> [Draw#] [TicketSerial/SuperPrize] [MORE link]
  
  Detail page: https://www.nlb.lk/results/<slug>/<draw_number>
    Contains the full winning numbers.

Usage:
  python nlb_scraper_pw.py --lottery "Mahajana Sampatha" --draws 100
  python nlb_scraper_pw.py --lottery "Mega Power"        --draws 50
  python nlb_scraper_pw.py --all                          # scrape all lotteries
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore

from rich.console import Console
from rich.progress import track
from rich.table import Table as RichTable

from nlb_database import (
    DB_PATH, LOTTERY_SLUGS, init_db, _insert_result, get_connection,
    _name_to_slug,
)

console = Console(highlight=False)

NLB_BASE = "https://www.nlb.lk"

# ---------------------------------------------------------------------------
# Playwright scraper
# ---------------------------------------------------------------------------

class PlaywrightNLBScraper:
    """Uses a headless Chromium browser to bypass nlb.lk's JS cookie wall."""

    def __init__(self, headless: bool = True):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self._page = self._browser.new_page()
        self._page.set_default_timeout(30_000)
        self._warmed = False

    def _warm_up(self, slug: str) -> None:
        """Load the listing page once to pass the JS cookie challenge."""
        if not self._warmed:
            url = f"{NLB_BASE}/results/{slug}"
            console.print(f"[dim]Warming up session: {url}[/dim]")
            self._page.goto(url, wait_until="networkidle", timeout=30_000)
            self._page.wait_for_timeout(2_000)
            self._warmed = True

    def scrape_listing(self, slug: str, max_draws: int = 100) -> list[dict]:
        """
        Scrape the listing table on /results/<slug>.
        Returns list of dicts: {draw_number, draw_date, ticket_serial, detail_url}
        """
        self._warm_up(slug)
        url = f"{NLB_BASE}/results/{slug}"
        console.print(f"[cyan]Scraping listing:[/cyan] {url}")
        self._page.goto(url, wait_until="networkidle", timeout=30_000)
        self._page.wait_for_timeout(2_000)

        rows = []
        page_num = 1

        while len(rows) < max_draws:
            # Get all table rows
            trs = self._page.query_selector_all("table tr")
            if not trs:
                console.print("[yellow]No table rows found.[/yellow]")
                break

            new_found = 0
            for tr in trs:
                tds = tr.query_selector_all("td")
                if len(tds) < 2:
                    continue

                draw_text = (tds[0].inner_text() or "").strip()
                # Draw number is on the first line; date on the second
                lines = [l.strip() for l in draw_text.splitlines() if l.strip()]
                if not lines or not lines[0].isdigit():
                    continue

                draw_number = int(lines[0])
                draw_date_raw = lines[1] if len(lines) > 1 else ""
                draw_date = _parse_date(draw_date_raw)

                serial_text = (tds[1].inner_text() or "").strip().splitlines()[0].strip()

                # Find the MORE link
                detail_url = ""
                links = tr.query_selector_all("a")
                for a in links:
                    href = a.get_attribute("href") or ""
                    txt  = (a.inner_text() or "").strip().upper()
                    if "MORE" in txt or slug in href.lower():
                        detail_url = href if href.startswith("http") else f"{NLB_BASE}/{href.lstrip('/')}"
                        break

                rows.append({
                    "draw_number": draw_number,
                    "draw_date":   draw_date or "",
                    "ticket_serial": serial_text,
                    "detail_url":  detail_url,
                })
                new_found += 1

                if len(rows) >= max_draws:
                    break

            console.print(f"  Page {page_num}: {new_found} rows collected (total: {len(rows)})")

            if len(rows) >= max_draws:
                break

            # Try to navigate to next page
            next_btn = (
                self._page.query_selector("a[rel='next']")
                or self._page.query_selector("li.next a")
                or self._page.query_selector("a:has-text('Next')")
                or self._page.query_selector("button:has-text('Next')")
            )
            if not next_btn:
                console.print("[dim]No next page found.[/dim]")
                break

            next_btn.click()
            self._page.wait_for_load_state("networkidle", timeout=20_000)
            self._page.wait_for_timeout(1_000)
            page_num += 1

        return rows

    def scrape_detail(self, detail_url: str, draw_number: int) -> Optional[dict]:
        """
        Fetch the detail page for a single draw.

        Confirmed DOM structure (nlb.lk 2026):
          Winning balls are <li> elements with class "Circle":
            <li class="Letter Circle Blue bT">T</li>   ← letter ball
            <li class="Number-1 Circle Yellow b4">4</li> ← digit ball
          Draw date is in:
            <h2>Prize Structure  - Saturday September 26, 2026</h2>
        """
        try:
            self._page.goto(detail_url, wait_until="networkidle", timeout=30_000)
            self._page.wait_for_timeout(1_500)
        except Exception as exc:
            console.print(f"[red]Detail page error (draw {draw_number}): {exc}[/red]")
            return None

        # ── Extract draw date from the h2 Prize Structure heading ──
        draw_date = ""
        try:
            h2_els = self._page.query_selector_all("h2")
            for h2 in h2_els:
                txt = (h2.inner_text() or "").strip()
                if "Prize Structure" in txt or "prize structure" in txt.lower():
                    # "Prize Structure  - Saturday September 26, 2026"
                    m = re.search(r"-\s*(.+)$", txt)
                    if m:
                        draw_date = _parse_date(m.group(1).strip()) or ""
                    break
        except Exception:
            pass

        # ── Extract winning balls from li.Circle elements ──
        # Structure: <li class="Letter Circle ...">T</li>
        #            <li class="Number-1 Circle ...">4</li>
        winning_tokens: list[str] = []
        try:
            ball_els = self._page.query_selector_all("li.Circle")
            for el in ball_els:
                txt = (el.inner_text() or "").strip().upper()
                # Only keep single letter or 1-2 digit values
                if re.match(r"^[A-Z]$", txt) or re.match(r"^\d{1,2}$", txt):
                    winning_tokens.append(txt)
        except Exception:
            pass

        # ── Fallback: look for li with class containing 'Letter' or 'Number' ──
        if not winning_tokens:
            try:
                for sel in ["li[class*='Letter']", "li[class*='Number']"]:
                    for el in self._page.query_selector_all(sel):
                        txt = (el.inner_text() or "").strip().upper()
                        if re.match(r"^[A-Z]$", txt) or re.match(r"^\d{1,2}$", txt):
                            winning_tokens.append(txt)
            except Exception:
                pass

        if not winning_tokens:
            return None

        return {
            "draw_number": draw_number,
            "draw_date":   draw_date or "",
            "winning_raw": " ".join(winning_tokens),
        }

    def close(self) -> None:
        try:
            self._browser.close()
            self._pw.stop()
        except Exception:
            pass


def _parse_date(text: str) -> Optional[str]:
    """Normalise a date string to YYYY-MM-DD."""
    if not text:
        return None
    text = text.strip()
    formats = [
        "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
        "%d %B %Y", "%d %b %Y",
        "%A %B %d, %Y",   # "Saturday September 26, 2026"
        "%B %d, %Y",
        "%b %d, %Y",
    ]
    for fmt in formats:
        try:
            from datetime import datetime
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Regex extraction
    m = re.search(r"(\w+ \w+ \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})", text)
    if m:
        return _parse_date(m.group(1))

    return None


# ---------------------------------------------------------------------------
# Main scrape-and-store function
# ---------------------------------------------------------------------------

def playwright_scrape_and_store(
    lottery_name: str,
    num_draws:    int    = 100,
    db_path:      Path   = DB_PATH,
    headless:     bool   = True,
) -> int:
    """
    Scrape the listing page and then fetch each detail URL to get winning numbers.
    Stores results in SQLite. Returns count of newly inserted rows.
    """
    init_db(db_path)
    slug    = _name_to_slug(lottery_name)
    scraper = PlaywrightNLBScraper(headless=headless)
    inserted = 0

    try:
        console.print(f"\n[bold cyan]Scraping[/bold cyan] '{lottery_name}' — {num_draws} draws…")

        # Step 1: get listing
        listing = scraper.scrape_listing(slug, max_draws=num_draws)
        if not listing:
            console.print("[red]No listing rows found.[/red]")
            return 0

        console.print(f"[green]Found {len(listing)} rows in listing.[/green]")

        # Step 2: for each row, fetch detail page
        conn = get_connection(db_path)
        skipped = 0

        for item in track(listing, description=f"Fetching {lottery_name} details…"):
            draw_no     = item["draw_number"]
            detail_url  = item["detail_url"]
            listing_date = item["draw_date"]

            if not detail_url:
                # Use the serial from listing as the "winning number" fallback
                if item["ticket_serial"]:
                    with conn:
                        row_id = _insert_result(
                            conn, lottery_name, draw_no,
                            listing_date or "1900-01-01",
                            item["ticket_serial"],
                        )
                    if row_id:
                        inserted += 1
                    else:
                        skipped += 1
                continue

            detail = scraper.scrape_detail(detail_url, draw_no)
            time.sleep(0.8)  # polite delay

            if detail:
                with conn:
                    row_id = _insert_result(
                        conn, lottery_name, draw_no,
                        detail["draw_date"] or listing_date or "1900-01-01",
                        detail["winning_raw"],
                    )
                if row_id:
                    inserted += 1
                else:
                    skipped += 1
            else:
                # Fallback: store the serial number from the listing
                if item["ticket_serial"]:
                    with conn:
                        row_id = _insert_result(
                            conn, lottery_name, draw_no,
                            listing_date or "1900-01-01",
                            item["ticket_serial"],
                        )
                    if row_id:
                        inserted += 1
                    else:
                        skipped += 1

        conn.close()
        console.print(
            f"[green]Done.[/green] Inserted [bold]{inserted}[/bold] new rows "
            f"(skipped {skipped} duplicates)."
        )

    finally:
        scraper.close()

    return inserted


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Playwright-based NLB scraper (bypasses JS cookie challenge)"
    )
    parser.add_argument("--lottery", help='Lottery name e.g. "Mahajana Sampatha"')
    parser.add_argument("--draws",   type=int, default=100, help="Number of draws (default: 100)")
    parser.add_argument("--all",     action="store_true",   help="Scrape all configured lotteries")
    parser.add_argument("--visible", action="store_true",   help="Show browser window (non-headless)")

    args = parser.parse_args()
    headless = not args.visible

    if args.all:
        for name in LOTTERY_SLUGS:
            try:
                playwright_scrape_and_store(name, args.draws, headless=headless)
            except Exception as exc:
                console.print(f"[red]Error scraping {name}: {exc}[/red]")
    elif args.lottery:
        playwright_scrape_and_store(args.lottery, args.draws, headless=headless)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

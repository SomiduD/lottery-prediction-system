"""
nlb_database.py
================
Module 1 – Data Scraping & Storage

Responsibilities:
  • Maintain an SQLite database of NLB lottery draw results.
  • Scrape historical results from nlb.lk via BeautifulSoup4 (with a
    srilanka-lottery helper as fallback).
  • Expose a CLI so you can manually insert / query records.

Usage (CLI):
  python nlb_database.py --help
  python nlb_database.py scrape  --lottery mahajana-sampatha --draws 50
  python nlb_database.py add     --lottery "Mahajana Sampatha" --draw 1234 --date 2024-01-15 --numbers "A 12 34 56 78"
  python nlb_database.py list    --lottery "Mahajana Sampatha" --limit 20
  python nlb_database.py lotteries
"""

from __future__ import annotations

import argparse
import sqlite3
import time
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import io
import os
import requests
from bs4 import BeautifulSoup
from rich.console import Console

# Force UTF-8 output on Windows so Rich Unicode symbols render correctly.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except AttributeError:
    pass
from rich.table import Table
from rich.progress import track

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = Path(__file__).parent / "nlb_results.db"
NLB_BASE = "https://www.nlb.lk/results"

# Mapping: human-readable lottery name → URL slug used on nlb.lk
LOTTERY_SLUGS: dict[str, str] = {
    "Mahajana Sampatha":  "mahajana-sampatha",
    "Hadahana":           "hadahana",
    "Mega Power":         "mega-power",
    "Dhana Nidhanaya":    "dhana-nidhanaya",
    "Shanida":            "shanida",
    "Jathika Sampatha":   "jathika-sampatha",
    "Supiri Vasana":      "supiri-vasana",
    "Govisetha":          "govisetha",
    "Neeroga":            "neeroga",
    "Divi Muthu":         "divi-muthu",
    "Kapruka":            "kapruka",
    "Soorya":             "soorya",
    "Vasana Sampatha":    "vasana-sampatha",
    "Lucky 7":            "lucky-7",
    "Ran Muthu Savari":   "ran-muthu-savari",
}

console = Console(highlight=False)

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Return a connection with row_factory set."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """
    Create the database schema if it does not exist yet.

    Table: lottery_results
    ┌──────────────┬──────────────────────────────────────────────┐
    │ Column       │ Description                                  │
    ├──────────────┼──────────────────────────────────────────────┤
    │ id           │ Auto-increment primary key                   │
    │ lottery_name │ Human-readable name  (e.g. "Mega Power")     │
    │ draw_number  │ Official draw number (integer)               │
    │ draw_date    │ Draw date as ISO-8601 text  (YYYY-MM-DD)     │
    │ winning_raw  │ Raw winning string  (e.g. "A 12 34 56 78")  │
    │ created_at   │ Row insertion timestamp                      │
    └──────────────┴──────────────────────────────────────────────┘

    Table: individual_numbers
    Normalised one-row-per-ball table for fast frequency queries.
    ┌──────────────┬──────────────────────────────────────────────┐
    │ result_id    │ FK → lottery_results.id                      │
    │ lottery_name │ Denormalised for query convenience           │
    │ token        │ A single ball / letter  (e.g. "A", "34")    │
    └──────────────┴──────────────────────────────────────────────┘
    """
    conn = get_connection(db_path)
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS lottery_results (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                lottery_name TEXT    NOT NULL,
                draw_number  INTEGER NOT NULL,
                draw_date    TEXT    NOT NULL,
                winning_raw  TEXT    NOT NULL,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE (lottery_name, draw_number)
            );

            CREATE TABLE IF NOT EXISTS individual_numbers (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                result_id    INTEGER NOT NULL REFERENCES lottery_results(id) ON DELETE CASCADE,
                lottery_name TEXT    NOT NULL,
                token        TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_ind_lottery_token
                ON individual_numbers (lottery_name, token);
        """)
    conn.close()
    console.print(f"[green]✓[/green] Database ready → [bold]{db_path}[/bold]")


# ---------------------------------------------------------------------------
# Internal insert helper
# ---------------------------------------------------------------------------

def _insert_result(
    conn: sqlite3.Connection,
    lottery_name: str,
    draw_number: int,
    draw_date: str,
    winning_raw: str,
) -> Optional[int]:
    """
    Insert a draw result row and its individual tokens.
    Returns the new row id, or None if it was a duplicate.
    """
    try:
        cur = conn.execute(
            """
            INSERT INTO lottery_results (lottery_name, draw_number, draw_date, winning_raw)
            VALUES (?, ?, ?, ?)
            """,
            (lottery_name, draw_number, draw_date, winning_raw.strip()),
        )
        result_id = cur.lastrowid

        # Parse tokens: split on whitespace; keep letters and numeric strings
        tokens = [t.strip() for t in winning_raw.split() if t.strip()]
        for token in tokens:
            conn.execute(
                """
                INSERT INTO individual_numbers (result_id, lottery_name, token)
                VALUES (?, ?, ?)
                """,
                (result_id, lottery_name, token.upper()),
            )
        return result_id

    except sqlite3.IntegrityError:
        # UNIQUE constraint: (lottery_name, draw_number) already exists
        return None


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

class NLBScraper:
    """
    Scrapes draw results from nlb.lk using BeautifulSoup4.

    Strategy:
      GET https://www.nlb.lk/results/<slug>?draw=<draw_number>
      The page contains a results card with the draw date and winning numbers.

    Fallback: if the HTML parse fails, we attempt the srilanka-lottery
    package which uses its own session/cookie management.
    """

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        # Warm up the session / accept cookies
        try:
            self.session.get(NLB_BASE, timeout=15)
        except requests.RequestException:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_draw(self, slug: str, draw_number: int) -> Optional[dict]:
        """
        Fetch a single draw result.

        Returns a dict:
          {
            "draw_number": int,
            "draw_date":   "YYYY-MM-DD",
            "winning_raw": "A 12 34 56 78",
          }
        or None if not found / parse failed.
        """
        url = f"{NLB_BASE}/{slug}?draw={draw_number}"
        try:
            resp = self.session.get(url, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            console.print(f"[red]HTTP error for draw {draw_number}:[/red] {exc}")
            return None

        return self._parse_result_page(resp.text, draw_number)

    def fetch_latest(self, slug: str, lottery_name: str, count: int = 50) -> list[dict]:
        """
        Attempt to fetch the *count* most-recent draws by scraping the
        results listing page (all draws shown in descending order).
        Falls back to iterating draw numbers if the listing parse fails.
        """
        results: list[dict] = []

        # First try: parse the results listing table
        try:
            results = self._scrape_listing(slug, lottery_name, count)
        except Exception as exc:
            console.print(f"[yellow]Listing scrape failed ({exc}), falling back to draw-by-draw.[/yellow]")

        if results:
            return results

        # Fallback: try the srilanka-lottery library
        return self._fetch_via_library(lottery_name, count)

    # ------------------------------------------------------------------
    # Internal parsers
    # ------------------------------------------------------------------

    def _parse_result_page(self, html: str, draw_number: int) -> Optional[dict]:
        """Parse a single draw result page."""
        soup = BeautifulSoup(html, "lxml")

        # Look for a date element – nlb.lk uses various class names
        date_text = ""
        for selector in [
            "span.draw-date", "div.draw-date", ".result-date",
            "[class*='draw-date']", "[class*='result-date']",
        ]:
            el = soup.select_one(selector)
            if el:
                date_text = el.get_text(strip=True)
                break

        # Parse date – handles "15 Jan 2024" or "2024-01-15"
        draw_date = self._parse_date(date_text) or datetime.now().strftime("%Y-%m-%d")

        # Winning numbers – often inside a results-ball div or span
        numbers: list[str] = []
        for selector in [
            ".result-ball", ".winning-number", ".ball",
            "[class*='result-ball']", "[class*='winning']",
            ".number-ball span", ".lottery-result td",
        ]:
            balls = soup.select(selector)
            if balls:
                numbers = [b.get_text(strip=True) for b in balls if b.get_text(strip=True)]
                break

        # Last-ditch: look for a <table> containing "winning"
        if not numbers:
            for table in soup.find_all("table"):
                header = table.get_text(" ", strip=True).lower()
                if "winning" in header or "number" in header:
                    tds = table.select("td")
                    candidates = [
                        td.get_text(strip=True)
                        for td in tds
                        if re.match(r"^[A-Za-z0-9]+$", td.get_text(strip=True))
                    ]
                    if candidates:
                        numbers = candidates
                        break

        if not numbers:
            return None

        return {
            "draw_number": draw_number,
            "draw_date":   draw_date,
            "winning_raw": " ".join(numbers),
        }

    def _scrape_listing(self, slug: str, lottery_name: str, count: int) -> list[dict]:
        """
        Scrape the paginated results listing at /results/<slug>.
        nlb.lk shows results in a table with columns: Draw | Date | Results.
        """
        results: list[dict] = []
        page = 1

        while len(results) < count:
            url = f"{NLB_BASE}/{slug}?page={page}"
            try:
                resp = self.session.get(url, timeout=20)
                resp.raise_for_status()
            except requests.RequestException as exc:
                console.print(f"[red]Listing page {page} error:[/red] {exc}")
                break

            soup = BeautifulSoup(resp.text, "lxml")

            # Locate the results table rows
            rows = soup.select("table.results-table tr, .result-row, .draw-row")
            if not rows:
                # Generic table fallback
                tables = soup.select("table")
                for t in tables:
                    rows = t.select("tr")
                    if len(rows) > 2:
                        break

            if not rows:
                break

            parsed_any = False
            for row in rows[1:]:  # skip header
                cells = row.select("td")
                if len(cells) < 3:
                    continue

                draw_num_text = cells[0].get_text(strip=True)
                date_text     = cells[1].get_text(strip=True)
                numbers_text  = cells[2].get_text(" ", strip=True)

                if not re.match(r"^\d+$", draw_num_text):
                    continue

                draw_date = self._parse_date(date_text)
                if not draw_date:
                    continue

                results.append({
                    "draw_number": int(draw_num_text),
                    "draw_date":   draw_date,
                    "winning_raw": numbers_text,
                })
                parsed_any = True

                if len(results) >= count:
                    break

            if not parsed_any:
                break

            # Check for a "next page" link
            next_link = soup.select_one("a[rel='next'], .pagination .next a")
            if not next_link:
                break

            page += 1
            time.sleep(1.0)  # polite delay

        return results

    def _fetch_via_library(self, lottery_name: str, count: int) -> list[dict]:
        """Use the srilanka-lottery library as a fallback."""
        try:
            from srilanka_lottery import scrape_nlb_result  # type: ignore
        except ImportError:
            console.print(
                "[yellow]srilanka-lottery not installed. "
                "Run: pip install srilanka-lottery[/yellow]"
            )
            return []

        slug = _name_to_slug(lottery_name)
        results: list[dict] = []

        # The library doesn't expose a "latest N" endpoint cleanly,
        # so we iterate from a high draw number downward.
        # We make a probe request to get the latest draw number first.
        probe = scrape_nlb_result(slug, "latest")
        if probe and "draw_number" in probe:
            latest_draw = int(probe["draw_number"])
        else:
            console.print("[yellow]Could not determine latest draw number.[/yellow]")
            return []

        for draw_no in track(
            range(latest_draw, max(1, latest_draw - count), -1),
            description=f"Fetching {lottery_name} via library…",
        ):
            data = scrape_nlb_result(slug, draw_no)
            if data and "winning" in str(data).lower():
                # Normalise the library's return format
                raw = data.get("results") or data.get("winning_numbers") or ""
                if isinstance(raw, list):
                    raw = " ".join(str(x) for x in raw)
                results.append({
                    "draw_number": draw_no,
                    "draw_date":   data.get("date", ""),
                    "winning_raw": str(raw),
                })
            time.sleep(0.5)

        return results

    @staticmethod
    def _parse_date(text: str) -> Optional[str]:
        """
        Normalise a date string to YYYY-MM-DD.
        Handles common formats found on nlb.lk.
        """
        if not text:
            return None

        formats = [
            "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
            "%d %B %Y", "%d %b %Y",
            "%B %d, %Y", "%b %d, %Y",
        ]
        text = text.strip()
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue

        # Try extracting date-like substring with regex
        m = re.search(r"(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})", text)
        if m:
            raw = m.group(1)
            for fmt in ["%Y-%m-%d", "%d/%m/%Y"]:
                try:
                    return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                except ValueError:
                    continue

        return None


# ---------------------------------------------------------------------------
# Helper: name ↔ slug
# ---------------------------------------------------------------------------

def _name_to_slug(name: str) -> str:
    """Convert a lottery name to its URL slug."""
    # First check the config dict (case-insensitive)
    for k, v in LOTTERY_SLUGS.items():
        if k.lower() == name.lower():
            return v
    # Generic fallback: lowercase, spaces → hyphens
    return name.lower().replace(" ", "-")


def _slug_to_name(slug: str) -> str:
    """Reverse of _name_to_slug – returns the slug as-is if not found."""
    for k, v in LOTTERY_SLUGS.items():
        if v == slug:
            return k
    return slug.replace("-", " ").title()


# ---------------------------------------------------------------------------
# High-level public functions (used by other modules)
# ---------------------------------------------------------------------------

def scrape_and_store(lottery_name: str, num_draws: int = 50, db_path: Path = DB_PATH) -> int:
    """
    Scrape *num_draws* historical results for *lottery_name* and store them.
    Returns the count of newly inserted rows.
    """
    init_db(db_path)
    slug    = _name_to_slug(lottery_name)
    scraper = NLBScraper()

    console.print(f"\n[bold cyan]Scraping[/bold cyan] '{lottery_name}' (slug: {slug}) – {num_draws} draws…")
    draws = scraper.fetch_latest(slug, lottery_name, num_draws)

    if not draws:
        console.print("[red]No results retrieved.[/red]")
        return 0

    inserted = 0
    conn = get_connection(db_path)
    with conn:
        for draw in draws:
            row_id = _insert_result(
                conn,
                lottery_name,
                draw["draw_number"],
                draw["draw_date"],
                draw["winning_raw"],
            )
            if row_id is not None:
                inserted += 1

    conn.close()
    console.print(f"[green]✓[/green] Inserted [bold]{inserted}[/bold] new rows (skipped {len(draws) - inserted} duplicates).")
    return inserted


def add_manual_result(
    lottery_name: str,
    draw_number:  int,
    draw_date:    str,
    winning_raw:  str,
    db_path: Path = DB_PATH,
) -> bool:
    """
    CLI-friendly helper to manually insert a draw result.
    Returns True if inserted, False if it was a duplicate.
    """
    init_db(db_path)
    conn = get_connection(db_path)
    with conn:
        row_id = _insert_result(conn, lottery_name, draw_number, draw_date, winning_raw)
    conn.close()

    if row_id is not None:
        console.print(f"[green]✓ Inserted draw #{draw_number} for '{lottery_name}'.[/green]")
        return True
    else:
        console.print(f"[yellow]Draw #{draw_number} for '{lottery_name}' already exists – skipped.[/yellow]")
        return False


def list_results(lottery_name: str, limit: int = 20, db_path: Path = DB_PATH) -> list[dict]:
    """Return the most recent *limit* draws for *lottery_name* as dicts."""
    conn = get_connection(db_path)
    rows = conn.execute(
        """
        SELECT draw_number, draw_date, winning_raw
        FROM   lottery_results
        WHERE  lottery_name = ?
        ORDER  BY draw_date DESC, draw_number DESC
        LIMIT  ?
        """,
        (lottery_name, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_scrape(args: argparse.Namespace) -> None:
    scrape_and_store(args.lottery, args.draws)


def _cmd_add(args: argparse.Namespace) -> None:
    add_manual_result(
        lottery_name=args.lottery,
        draw_number=args.draw,
        draw_date=args.date,
        winning_raw=args.numbers,
    )


def _cmd_list(args: argparse.Namespace) -> None:
    rows = list_results(args.lottery, args.limit)
    if not rows:
        console.print(f"[yellow]No results found for '{args.lottery}'.[/yellow]")
        return

    table = Table(title=f"Latest results – {args.lottery}", show_lines=True)
    table.add_column("Draw #",   style="cyan",  justify="right")
    table.add_column("Date",     style="white")
    table.add_column("Numbers",  style="green")
    for r in rows:
        table.add_row(str(r["draw_number"]), r["draw_date"], r["winning_raw"])
    console.print(table)


def _cmd_lotteries(_args: argparse.Namespace) -> None:
    table = Table(title="Configured Lotteries", show_lines=True)
    table.add_column("Name",  style="cyan")
    table.add_column("Slug",  style="white")
    for name, slug in sorted(LOTTERY_SLUGS.items()):
        table.add_row(name, slug)
    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NLB Lottery Database – scrape & manage draw results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- scrape ---
    p_scrape = sub.add_parser("scrape", help="Scrape historical results from nlb.lk")
    p_scrape.add_argument("--lottery", required=True,
                          help='Lottery name, e.g. "Mahajana Sampatha"')
    p_scrape.add_argument("--draws", type=int, default=50,
                          help="Number of draws to fetch (default: 50)")
    p_scrape.set_defaults(func=_cmd_scrape)

    # --- add ---
    p_add = sub.add_parser("add", help="Manually add a draw result")
    p_add.add_argument("--lottery", required=True,  help='Lottery name')
    p_add.add_argument("--draw",    required=True,  type=int,  help="Draw number")
    p_add.add_argument("--date",    required=True,  help="Date YYYY-MM-DD")
    p_add.add_argument("--numbers", required=True,
                       help='Winning numbers/letters, e.g. "A 12 34 56 78"')
    p_add.set_defaults(func=_cmd_add)

    # --- list ---
    p_list = sub.add_parser("list", help="Show recent results from the database")
    p_list.add_argument("--lottery", required=True,  help='Lottery name')
    p_list.add_argument("--limit",   type=int, default=20, help="Rows to show")
    p_list.set_defaults(func=_cmd_list)

    # --- lotteries ---
    p_lot = sub.add_parser("lotteries", help="Show configured lottery names & slugs")
    p_lot.set_defaults(func=_cmd_lotteries)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

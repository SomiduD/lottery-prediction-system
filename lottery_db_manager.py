"""
lottery_db_manager.py
======================
Manages per-lottery SQLite databases stored in c:/Lottery/data/.

Each lottery gets its own .db file with a richer schema:
  draws (id, draw_number, draw_date, winning_raw, letter, n1..n6,
         day_of_week, day_name, scraped_at)

This module also migrates all existing data from nlb_results.db
into the per-lottery databases.

Usage:
  python lottery_db_manager.py init          -- create all lottery DBs
  python lottery_db_manager.py migrate       -- copy nlb_results.db → per-lottery DBs
  python lottery_db_manager.py status        -- show row counts per lottery
  python lottery_db_manager.py show --lottery "Mega Power" --limit 20
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

# ─── Paths ────────────────────────────────────────────────────────────────────

ROOT_DIR    = Path(__file__).parent
DATA_DIR    = ROOT_DIR / "data"
OLD_DB_PATH = ROOT_DIR / "nlb_results.db"

DAY_NAMES = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

# All lotteries we track → their URL slugs
LOTTERIES: dict[str, str] = {
    "Mahajana Sampatha": "mahajana-sampatha",
    "Handahana":         "handahana",
    "Ada Sampatha":      "ada-sampatha",
    "Govisetha":         "govisetha",
    "Mega Power":        "mega-power",
    "Dhana Nidhanaya":   "dhana-nidhanaya",
    "Jaya":              "jaya",
    "Shanida":           "shanida",
    "Jathika Sampatha":  "jathika-sampatha",
    "Supiri Vasana":     "supiri-vasana",
    "Neeroga":           "neeroga",
    "Divi Muthu":        "divi-muthu",
    "Kapruka":           "kapruka",
    "Soorya":            "soorya",
    "Vasana Sampatha":   "vasana-sampatha",
    "Lucky 7":           "lucky-7",
    "Ran Muthu Savari":  "ran-muthu-savari",
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def lottery_db_path(lottery_name: str) -> Path:
    """Return the .db file path for a given lottery name."""
    safe = re.sub(r"[^a-z0-9]+", "_", lottery_name.lower()).strip("_")
    return DATA_DIR / f"{safe}.db"


def parse_winning_raw(winning_raw: str) -> dict:
    """
    Parse a winning_raw string like 'T 4 3 4 1 9 4' or 'A 10 06 22 44'
    into a dict: {letter, n1, n2, n3, n4, n5, n6}
    """
    tokens = winning_raw.strip().upper().split()
    letter = None
    numbers = []
    for t in tokens:
        if re.match(r"^[A-Z]$", t):
            letter = t
        elif re.match(r"^\d{1,2}$", t):
            numbers.append(int(t))
    # Pad to 6 numbers
    while len(numbers) < 6:
        numbers.append(None)
    return {
        "letter": letter,
        "n1": numbers[0] if len(numbers) > 0 else None,
        "n2": numbers[1] if len(numbers) > 1 else None,
        "n3": numbers[2] if len(numbers) > 2 else None,
        "n4": numbers[3] if len(numbers) > 3 else None,
        "n5": numbers[4] if len(numbers) > 4 else None,
        "n6": numbers[5] if len(numbers) > 5 else None,
    }


def draw_date_to_dow(draw_date: str) -> tuple[Optional[int], Optional[str]]:
    """Return (day_of_week_int 0=Mon, day_name) from a YYYY-MM-DD string."""
    try:
        dt = datetime.strptime(draw_date[:10], "%Y-%m-%d")
        dow = dt.weekday()
        return dow, DAY_NAMES[dow]
    except Exception:
        return None, None


# ─── DB init ──────────────────────────────────────────────────────────────────

DRAWS_SCHEMA = """
CREATE TABLE IF NOT EXISTS draws (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    draw_number     INTEGER NOT NULL UNIQUE,
    draw_date       TEXT NOT NULL,
    winning_raw     TEXT NOT NULL,
    letter          TEXT,
    n1 INTEGER, n2 INTEGER, n3 INTEGER,
    n4 INTEGER, n5 INTEGER, n6 INTEGER,
    day_of_week     INTEGER,   -- 0=Mon .. 6=Sun
    day_name        TEXT,

    -- Computed analytics columns (populated on insert)
    num_sum         INTEGER,   -- sum of all numbers
    num_min         INTEGER,   -- smallest number
    num_max         INTEGER,   -- largest number
    num_range       INTEGER,   -- max - min
    odd_count       INTEGER,   -- count of odd numbers
    even_count      INTEGER,   -- count of even numbers
    has_consecutive INTEGER,   -- 1 if any two consecutive numbers appear
    avg_num         REAL,      -- average of numbers
    low_count       INTEGER,   -- numbers 0-4
    high_count      INTEGER,   -- numbers 5-9

    scraped_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_draws_date    ON draws(draw_date);
CREATE INDEX IF NOT EXISTS idx_draws_dow     ON draws(day_of_week);
CREATE INDEX IF NOT EXISTS idx_draws_number  ON draws(draw_number DESC);
CREATE INDEX IF NOT EXISTS idx_draws_letter  ON draws(letter);
CREATE INDEX IF NOT EXISTS idx_draws_sum     ON draws(num_sum);
"""

META_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def init_lottery_db(lottery_name: str) -> Path:
    """
    Create (or verify) the per-lottery SQLite database.
    Returns the path.
    """
    DATA_DIR.mkdir(exist_ok=True)
    db_path = lottery_db_path(lottery_name)
    conn = sqlite3.connect(db_path)
    conn.executescript(DRAWS_SCHEMA)
    conn.executescript(META_SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("lottery_name", lottery_name)
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("slug", LOTTERIES.get(lottery_name, lottery_name.lower().replace(" ", "-")))
    )
    conn.commit()
    conn.close()
    return db_path


def init_all() -> None:
    """Create all per-lottery databases."""
    DATA_DIR.mkdir(exist_ok=True)
    print(f"\n  Creating {len(LOTTERIES)} lottery databases in {DATA_DIR}...\n")
    for name in LOTTERIES:
        path = init_lottery_db(name)
        print(f"  ✓  {name:<25} → {path.name}")
    print()


# ─── Insert helper ────────────────────────────────────────────────────────────

def insert_draw(
    conn: sqlite3.Connection,
    draw_number: int,
    draw_date: str,
    winning_raw: str,
    scraped_at: Optional[str] = None,
) -> bool:
    """
    Insert a single draw row with computed analytics columns.
    Returns True if inserted, False if duplicate.
    """
    parsed = parse_winning_raw(winning_raw)
    dow, day_name = draw_date_to_dow(draw_date)

    # Compute analytics from the parsed numbers
    nums = [parsed[f"n{i}"] for i in range(1, 7) if parsed.get(f"n{i}") is not None]
    if nums:
        num_sum    = sum(nums)
        num_min    = min(nums)
        num_max    = max(nums)
        num_range  = num_max - num_min
        odd_count  = sum(1 for n in nums if n % 2 != 0)
        even_count = len(nums) - odd_count
        avg_num    = round(sum(nums) / len(nums), 2)
        low_count  = sum(1 for n in nums if n <= 4)
        high_count = sum(1 for n in nums if n >= 5)
        sn = sorted(nums)
        has_consecutive = int(any(sn[i+1] - sn[i] == 1 for i in range(len(sn)-1)))
    else:
        num_sum = num_min = num_max = num_range = None
        odd_count = even_count = low_count = high_count = None
        avg_num = has_consecutive = None

    try:
        conn.execute(
            """INSERT INTO draws
               (draw_number, draw_date, winning_raw,
                letter, n1, n2, n3, n4, n5, n6,
                day_of_week, day_name,
                num_sum, num_min, num_max, num_range,
                odd_count, even_count, has_consecutive,
                avg_num, low_count, high_count,
                scraped_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                draw_number, draw_date, winning_raw.strip(),
                parsed["letter"],
                parsed["n1"], parsed["n2"], parsed["n3"],
                parsed["n4"], parsed["n5"], parsed["n6"],
                dow, day_name,
                num_sum, num_min, num_max, num_range,
                odd_count, even_count, has_consecutive,
                avg_num, low_count, high_count,
                scraped_at or datetime.now().isoformat(),
            )
        )
        return True
    except sqlite3.IntegrityError:
        return False  # duplicate draw_number


# ─── Migration from nlb_results.db ───────────────────────────────────────────

def migrate_from_old_db() -> None:
    """
    Copy all rows from nlb_results.db (lottery_results + daily_results)
    into the per-lottery databases.
    """
    if not OLD_DB_PATH.exists():
        print(f"  [!] {OLD_DB_PATH} not found — nothing to migrate.")
        return

    print(f"\n  Migrating from {OLD_DB_PATH}...\n")
    old_conn = sqlite3.connect(OLD_DB_PATH)
    old_conn.row_factory = sqlite3.Row

    total_inserted = 0
    total_skipped  = 0
    per_lottery: dict[str, tuple[int, int]] = {}

    # ── lottery_results table ────────────────────────────────────────────────
    try:
        rows = old_conn.execute(
            "SELECT lottery_name, draw_number, draw_date, winning_raw, created_at "
            "FROM lottery_results ORDER BY draw_number"
        ).fetchall()
        print(f"  lottery_results: {len(rows)} rows")

        for row in rows:
            name = row["lottery_name"]
            init_lottery_db(name)
            path = lottery_db_path(name)
            conn = sqlite3.connect(path)
            ok = insert_draw(
                conn,
                draw_number = int(row["draw_number"]),
                draw_date   = row["draw_date"] or "1900-01-01",
                winning_raw = row["winning_raw"],
                scraped_at  = row["created_at"],
            )
            conn.commit()
            conn.close()

            ins, skp = per_lottery.get(name, (0, 0))
            if ok:
                per_lottery[name] = (ins + 1, skp)
                total_inserted += 1
            else:
                per_lottery[name] = (ins, skp + 1)
                total_skipped += 1

    except Exception as e:
        print(f"  [!] lottery_results migration error: {e}")

    # ── daily_results table ───────────────────────────────────────────────────
    try:
        rows2 = old_conn.execute(
            "SELECT lottery_name, draw_number, draw_date, winning_raw, scraped_at "
            "FROM daily_results ORDER BY draw_date"
        ).fetchall()
        print(f"  daily_results:   {len(rows2)} rows")

        for row in rows2:
            name = row["lottery_name"]
            # Try to use draw_number; daily_results often has it as TEXT or empty
            raw_dn = row["draw_number"]
            try:
                draw_number = int(raw_dn) if raw_dn else 0
            except (ValueError, TypeError):
                draw_number = 0

            if draw_number == 0:
                continue  # skip if no valid draw number

            init_lottery_db(name)
            path = lottery_db_path(name)
            conn = sqlite3.connect(path)
            ok = insert_draw(
                conn,
                draw_number = draw_number,
                draw_date   = row["draw_date"] or "1900-01-01",
                winning_raw = row["winning_raw"],
                scraped_at  = row["scraped_at"],
            )
            conn.commit()
            conn.close()

            ins, skp = per_lottery.get(name, (0, 0))
            if ok:
                per_lottery[name] = (ins + 1, skp)
                total_inserted += 1
            else:
                per_lottery[name] = (ins, skp + 1)
                total_skipped += 1

    except Exception as e:
        print(f"  [!] daily_results migration error: {e}")

    old_conn.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n  {'Lottery':<28}  {'Inserted':>8}  {'Skipped':>7}")
    print("  " + "-" * 48)
    for name, (ins, skp) in sorted(per_lottery.items()):
        print(f"  {name:<28}  {ins:>8}  {skp:>7}")
    print(f"\n  Total inserted: {total_inserted}  |  Skipped (duplicates): {total_skipped}\n")


# ─── Status / Show ────────────────────────────────────────────────────────────

def show_status() -> None:
    """Print row counts and date range for each lottery database."""
    DATA_DIR.mkdir(exist_ok=True)
    print(f"\n  {'Lottery':<28}  {'Draws':>6}  {'Oldest':>12}  {'Latest':>12}  {'DB File'}")
    print("  " + "-" * 80)

    for name in sorted(LOTTERIES):
        path = lottery_db_path(name)
        if not path.exists():
            print(f"  {name:<28}  {'—':>6}  {'—':>12}  {'—':>12}  (not created)")
            continue
        try:
            conn = sqlite3.connect(path)
            row = conn.execute(
                "SELECT COUNT(*), MIN(draw_date), MAX(draw_date) FROM draws"
            ).fetchone()
            conn.close()
            cnt, oldest, latest = row
            print(f"  {name:<28}  {cnt:>6}  {(oldest or '—'):>12}  {(latest or '—'):>12}  {path.name}")
        except Exception as e:
            print(f"  {name:<28}  ERROR: {e}")
    print()


def show_draws(lottery_name: str, limit: int = 20) -> None:
    """Print the most recent draws for a lottery."""
    path = lottery_db_path(lottery_name)
    if not path.exists():
        print(f"  [!] Database not found: {path}")
        return
    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT draw_number, draw_date, day_name, winning_raw "
        "FROM draws ORDER BY draw_number DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()

    print(f"\n  Last {limit} draws — {lottery_name}\n")
    print(f"  {'Draw#':>6}  {'Date':>12}  {'Day':>11}  Numbers")
    print("  " + "-" * 60)
    for draw_num, draw_date, day_name, winning_raw in rows:
        print(f"  {draw_num:>6}  {draw_date:>12}  {(day_name or '?'):>11}  {winning_raw}")
    print()


def get_max_draw_number(lottery_name: str) -> int:
    """Return the highest draw_number stored for a lottery (0 if none)."""
    path = lottery_db_path(lottery_name)
    if not path.exists():
        return 0
    conn = sqlite3.connect(path)
    row = conn.execute("SELECT MAX(draw_number) FROM draws").fetchone()
    conn.close()
    return row[0] or 0


def get_draw_count(lottery_name: str) -> int:
    """Return total number of draws stored for a lottery."""
    path = lottery_db_path(lottery_name)
    if not path.exists():
        return 0
    conn = sqlite3.connect(path)
    row = conn.execute("SELECT COUNT(*) FROM draws").fetchone()
    conn.close()
    return row[0] or 0


def load_all_draws(lottery_name: str) -> list[dict]:
    """
    Load all draws for a lottery as a list of dicts.
    Sorted by draw_number descending (newest first).
    """
    path = lottery_db_path(lottery_name)
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM draws ORDER BY draw_number DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Per-lottery database manager"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init",    help="Create all per-lottery databases")
    sub.add_parser("migrate", help="Migrate data from nlb_results.db → per-lottery DBs")
    sub.add_parser("status",  help="Show row counts for all lottery DBs")

    p_show = sub.add_parser("show", help="Show recent draws for one lottery")
    p_show.add_argument("--lottery", required=True, help='Lottery name e.g. "Mega Power"')
    p_show.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()

    if args.cmd == "init":
        init_all()
    elif args.cmd == "migrate":
        init_all()
        migrate_from_old_db()
    elif args.cmd == "status":
        show_status()
    elif args.cmd == "show":
        show_draws(args.lottery, args.limit)


if __name__ == "__main__":
    main()

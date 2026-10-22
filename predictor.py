"""
predictor.py  (v2 — ML-enhanced)
===================================
Multi-factor scoring engine for NLB lottery tickets.

Scoring formula (weighted combination):
  1. All-time frequency      (35%) — how often each token appeared historically
  2. Day-of-week frequency   (30%) — how often each token appears on THIS day of week
  3. Recent momentum         (25%) — frequency in the last 14 draws  
  4. Number range bias       (10%) — are high/low numbers currently trending?

Usage (programmatic):
  from predictor import score_tickets
  ranked = score_tickets("Mahajana Sampatha", ["A 1 2 3 4 5 6", ...])
"""

from __future__ import annotations
import argparse, sqlite3, re, sys, math
from collections import Counter
from pathlib import Path
from typing import Optional
from datetime import datetime, date

import pandas as pd
from rich.console import Console
from rich.table import Table

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

DB_PATH = Path(__file__).parent / "nlb_results.db"
console = Console(highlight=False)

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# ── Data access ──────────────────────────────────────────────────────────────

def _get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}\nRun nlb_scraper_pw.py first.")
    return sqlite3.connect(db_path)


def _load_tokens_with_meta(lottery_name: str, db_path: Path = DB_PATH) -> pd.DataFrame:
    """
    Load tokens with draw metadata. Falls back to cross-lottery data if the
    specific lottery has no rows (e.g. Handahana not yet scraped).

    Real DB schema (confirmed 2026-10-02):
      lottery_results:    id, lottery_name, draw_number, draw_date, winning_raw, created_at
      individual_numbers: id, result_id, lottery_name, token
    """
    conn = _get_conn(db_path)

    # ── Try individual_numbers for THIS lottery (best case) ─────────────────
    try:
        df = pd.read_sql_query("""
            SELECT lr.draw_date, lr.draw_number, inb.token,
                   NULL as day_of_week, NULL as draw_time
            FROM   individual_numbers inb
            JOIN   lottery_results lr ON lr.id = inb.result_id
            WHERE  inb.lottery_name = ?
        """, conn, params=(lottery_name,))
        if not df.empty:
            # Compute day_of_week from draw_date
            df["day_of_week"] = pd.to_datetime(df["draw_date"], errors="coerce").dt.dayofweek
            conn.close()
            return df
    except Exception:
        pass

    # ── Rebuild from winning_raw for THIS lottery ────────────────────────────
    try:
        df_res = pd.read_sql_query("""
            SELECT draw_date, draw_number, winning_raw
            FROM   lottery_results
            WHERE  lottery_name = ?
            ORDER  BY draw_date DESC
        """, conn, params=(lottery_name,))
    except Exception:
        df_res = pd.DataFrame()

    # ── Cross-lottery fallback if this lottery has no data ───────────────────
    if df_res.empty:
        console.print(f"[yellow]Warning:[/yellow] No data for '{lottery_name}'. "
                      f"Using cross-lottery patterns from all available draws.")
        # Use ALL tokens from individual_numbers (any lottery)
        try:
            df_all = pd.read_sql_query("""
                SELECT lr.draw_date, lr.draw_number, inb.token
                FROM   individual_numbers inb
                JOIN   lottery_results lr ON lr.id = inb.result_id
                ORDER  BY lr.draw_date DESC LIMIT 1000
            """, conn)
            conn.close()
            if not df_all.empty:
                df_all["day_of_week"] = pd.to_datetime(df_all["draw_date"], errors="coerce").dt.dayofweek
                return df_all
        except Exception:
            pass
        conn.close()
        raise ValueError("Database has no lottery data. Run the scraper first.")

    conn.close()

    # Expand winning_raw → individual tokens
    rows = []
    for _, row in df_res.iterrows():
        tokens = re.findall(r'\b([A-Z0-9]+)\b', str(row.get("winning_raw", "")).upper())
        dow = None
        try:
            dow = pd.to_datetime(row["draw_date"], errors="coerce").dayofweek
        except Exception:
            pass
        for t in tokens:
            rows.append({"draw_date": row["draw_date"], "day_of_week": dow,
                         "draw_time": None, "draw_number": row.get("draw_number"), "token": t})
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError(f"winning_raw was empty for '{lottery_name}'.")
    return df




def _parse_ticket(ticket: str) -> list[str]:
    """Parse ticket string into individual tokens."""
    ticket = ticket.strip().upper()
    parts  = ticket.split()
    if len(parts) == 1:
        m = re.match(r"^([A-Z]*)(\d+)$", parts[0])
        if m:
            letter_part, digit_part = m.group(1), m.group(2)
            tokens = list(letter_part) if letter_part else []
            for i in range(0, len(digit_part), 2):
                tokens.append(digit_part[i:i+2].lstrip("0") or "0")
            return tokens
    return [t for t in parts if t]


# ── Scoring engines ──────────────────────────────────────────────────────────

def _freq_table(df: pd.DataFrame) -> dict[str, float]:
    """Convert a DataFrame of tokens to a normalized frequency table."""
    counts = Counter(df["token"].str.upper().tolist())
    total  = sum(counts.values()) or 1
    return {t: c / total for t, c in counts.items()}


def _score_tokens(tokens: list[str], freq_table: dict[str, float], smoothing: float = 1e-6) -> float:
    """Log-probability score for a list of tokens given a frequency table."""
    score = 0.0
    for t in tokens:
        score += math.log(freq_table.get(t, smoothing))
    return score


def score_tickets(
    lottery_name:   str,
    ticket_numbers: list[str],
    db_path:        Path = DB_PATH,
    top_n:          Optional[int] = None,
    target_date:    Optional[date] = None,
) -> list[tuple[str, float]]:
    """
    Score tickets using multi-factor ML-inspired weighted model.

    Factors:
      W1=0.35  All-time frequency
      W2=0.30  Day-of-week frequency (same weekday as today)
      W3=0.25  Recent momentum (last 14 draws)
      W4=0.10  Number-range trend (are high numbers hot right now?)

    Returns sorted list of (ticket, score 0-100), best first.
    """
    if not ticket_numbers:
        return []

    # Load data
    try:
        df = _load_tokens_with_meta(lottery_name, db_path)
    except Exception as e:
        console.print(f"[yellow]Warning:[/yellow] {e}. Using uniform scores.")
        return [(t, 50.0) for t in ticket_numbers]

    # Today's day of week
    today_dow = (target_date or date.today()).weekday()  # 0=Mon … 6=Sun

    # ── Build frequency tables ──────────────────────────────────────────────

    # W1: All-time
    freq_all = _freq_table(df)

    # W2: Same day-of-week
    df_dow = df[df["day_of_week"].astype(str) == str(today_dow)]
    if df_dow.empty:
        # No draws on this weekday yet — fall back to all-time
        freq_dow = freq_all
        w2_weight = 0.0   # skip day-of-week factor
    else:
        freq_dow  = _freq_table(df_dow)
        w2_weight = 0.30

    # W3: Recent 14 draws momentum
    recent_dates = df["draw_date"].dropna().unique()
    recent_dates_sorted = sorted(recent_dates, reverse=True)[:14]
    df_recent = df[df["draw_date"].isin(recent_dates_sorted)]
    freq_recent = _freq_table(df_recent) if not df_recent.empty else freq_all

    # W4: High vs low number trend (last 7 draws)
    recent7 = df[df["draw_date"].isin(sorted(recent_dates, reverse=True)[:7])]
    numeric_tokens = pd.to_numeric(recent7["token"], errors="coerce").dropna()
    trend_high = float(numeric_tokens.mean()) if not numeric_tokens.empty else 50.0
    # trend_high > 40 means high numbers are currently dominant

    # Adjusted weights based on data availability
    w_remaining = 1.0 - w2_weight
    w1 = w_remaining * 0.535   # ≈ 0.35 when dow available, else 0.535
    w3 = w_remaining * 0.385   # ≈ 0.25 when dow available, else 0.385
    w4 = w_remaining * 0.080   # ≈ 0.10 when dow available, else 0.080
    if w2_weight == 0.0:
        w1, w3, w4 = 0.60, 0.30, 0.10

    smoothing = min(freq_all.values()) / 2 if freq_all else 1e-9

    raw_scores: list[tuple[str, float]] = []
    for ticket in ticket_numbers:
        tokens = _parse_ticket(ticket)
        if not tokens:
            raw_scores.append((ticket, -999.0))
            continue

        s1 = _score_tokens(tokens, freq_all,    smoothing)
        s2 = _score_tokens(tokens, freq_dow,    smoothing)
        s3 = _score_tokens(tokens, freq_recent, smoothing)

        # W4: bonus if ticket numbers align with current high/low trend
        ticket_nums = [int(t) for t in tokens if t.isdigit()]
        if ticket_nums:
            avg_ticket_num = sum(ticket_nums) / len(ticket_nums)
            # If high numbers are trending (mean > 40) and ticket has high nums → bonus
            trend_alignment = 1.0 - abs(avg_ticket_num - trend_high) / 99.0
        else:
            trend_alignment = 0.5

        combined = w1*s1 + w2_weight*s2 + w3*s3 + w4*trend_alignment*10
        raw_scores.append((ticket, combined))

    # Normalize to 0–100
    values = [s for _, s in raw_scores]
    lo, hi = min(values), max(values)
    rng    = hi - lo or 1

    normalized = sorted(
        [(t, (s - lo) / rng * 100) for t, s in raw_scores],
        key=lambda x: -x[1]
    )
    return normalized[:top_n] if top_n else normalized


# ── Analysis helpers ─────────────────────────────────────────────────────────

def get_hottest_tokens(lottery_name: str, top_n: int = 10, db_path: Path = DB_PATH):
    df = _load_tokens_with_meta(lottery_name, db_path)
    counts = Counter(df["token"].str.upper())
    total  = sum(counts.values()) or 1
    return [(t, c, round(c/total*100, 2)) for t, c in counts.most_common(top_n)]


def get_coldest_tokens(lottery_name: str, top_n: int = 10, db_path: Path = DB_PATH):
    df = _load_tokens_with_meta(lottery_name, db_path)
    counts = Counter(df["token"].str.upper())
    total  = sum(counts.values()) or 1
    least  = counts.most_common()[:-top_n-1:-1]
    return [(t, c, round(c/total*100, 2)) for t, c in least]


def get_dow_analysis(lottery_name: str, db_path: Path = DB_PATH) -> dict:
    """
    Returns per-day-of-week top tokens.
    { day_name: [(token, pct), ...] }
    """
    df = _load_tokens_with_meta(lottery_name, db_path)
    result = {}
    for dow in range(7):
        sub = df[df["day_of_week"].astype(str) == str(dow)]
        if sub.empty:
            continue
        counts = Counter(sub["token"].str.upper())
        total  = sum(counts.values()) or 1
        top5   = [(t, round(c/total*100, 1)) for t, c in counts.most_common(5)]
        result[DAY_NAMES[dow]] = top5
    return result


def get_draw_count(lottery_name: str, db_path: Path = DB_PATH) -> int:
    conn = _get_conn(db_path)
    row  = conn.execute("SELECT COUNT(*) FROM lottery_results WHERE lottery_name=?", (lottery_name,)).fetchone()
    conn.close()
    return row[0] if row else 0


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cmd_score(args):
    ranked = score_tickets(args.lottery, args.tickets)
    t = Table(title=f"Ticket Scores – {args.lottery}", show_lines=True)
    t.add_column("Rank", style="dim",   justify="center", width=6)
    t.add_column("Ticket", style="cyan")
    t.add_column("Score",  style="green",  justify="right")
    t.add_column("Grade",  style="yellow", justify="center")
    for rank, (ticket, score) in enumerate(ranked, 1):
        grade = "⭐⭐⭐" if score >= 70 else "⭐⭐" if score >= 40 else "⭐"
        t.add_row(str(rank), ticket, f"{score:.2f}", grade)
    console.print(t)

def _cmd_hot(args):
    tokens = get_hottest_tokens(args.lottery, args.top)
    t = Table(title=f"Hottest – {args.lottery} ({DAY_NAMES[date.today().weekday()]})", show_lines=True)
    t.add_column("Token", style="cyan", justify="center")
    t.add_column("Count", justify="right")
    t.add_column("%",     style="green", justify="right")
    for token, count, pct in tokens:
        t.add_row(token, str(count), f"{pct:.2f}%")
    console.print(t)

def _cmd_cold(args):
    tokens = get_coldest_tokens(args.lottery, args.top)
    t = Table(title=f"Coldest – {args.lottery}", show_lines=True)
    t.add_column("Token", style="magenta", justify="center")
    t.add_column("Count", justify="right")
    t.add_column("%",     style="red", justify="right")
    for token, count, pct in tokens:
        t.add_row(token, str(count), f"{pct:.2f}%")
    console.print(t)

def _cmd_dow(args):
    analysis = get_dow_analysis(args.lottery)
    for day, tokens in analysis.items():
        console.print(f"\n[bold]{day}[/bold]: " + ", ".join(f"{t}({p}%)" for t, p in tokens))

def _cmd_info(args):
    count = get_draw_count(args.lottery)
    console.print(f"[bold]{args.lottery}[/bold] – [cyan]{count}[/cyan] draws in database.")


def main():
    parser = argparse.ArgumentParser(description="ML prediction engine for NLB lotteries")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in [("score","Score ticket numbers"), ("hot","Hottest tokens"),
                             ("cold","Coldest tokens"), ("dow","Day-of-week analysis"), ("info","Draw count")]:
        p2 = sub.add_parser(name, help=help_text)
        p2.add_argument("--lottery", required=True)
        if name == "score":
            p2.add_argument("--tickets", required=True, nargs="+")
        if name in ("hot","cold"):
            p2.add_argument("--top", type=int, default=10)
        p2.set_defaults(func=locals()[f"_cmd_{name}"])

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()

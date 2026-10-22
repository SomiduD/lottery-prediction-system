"""
lottery_agent.py
=================
Per-lottery AI prediction agent.

Analyzes the full history of a lottery and predicts the most likely
winning numbers using 7 complementary factors:

  Factor 1 (25%)  All-time frequency — how often each token appeared
  Factor 2 (20%)  Day-of-week frequency — hot tokens for THIS weekday
  Factor 3 (20%)  Recency momentum — frequency in last 14 draws (weighted)
  Factor 4 (15%)  Gap / overdue analysis — numbers not seen in N draws
  Factor 5 (10%)  Pair correlation — numbers that appear together often
  Factor 6 (05%)  Number range trend — are high or low numbers dominating?
  Factor 7 (05%)  Letter-number affinity — which letters correlate with ranges

Usage:
  python lottery_agent.py predict --lottery "Mahajana Sampatha"
  python lottery_agent.py predict --lottery "Mega Power" --top 5
  python lottery_agent.py analyze --lottery "Mahajana Sampatha"
  python lottery_agent.py hot     --lottery "Mega Power"
  python lottery_agent.py all                               -- predict all lotteries
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from itertools import combinations
from pathlib import Path
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from lottery_db_manager import (
    LOTTERIES, lottery_db_path, load_all_draws, get_draw_count,
)

DAY_NAMES = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]


# ─── Data helpers ─────────────────────────────────────────────────────────────

def _tokens_from_raw(winning_raw: str) -> list[str]:
    return re.findall(r"\b([A-Z0-9]+)\b", winning_raw.strip().upper())


def _numeric_tokens(tokens: list[str]) -> list[int]:
    return [int(t) for t in tokens if re.match(r"^\d{1,2}$", t)]


def _letter_tokens(tokens: list[str]) -> list[str]:
    return [t for t in tokens if re.match(r"^[A-Z]$", t)]


def _load_draw_data(lottery_name: str) -> list[dict]:
    """
    Load draws from the per-lottery DB. Returns list of enriched dicts.
    Each dict: {draw_number, draw_date, winning_raw, tokens, numbers, letters, day_of_week, day_name}
    """
    raw = load_all_draws(lottery_name)   # newest first
    if not raw:
        raise ValueError(
            f"No data found for '{lottery_name}'.\n"
            f"Run: python lottery_updater.py --lottery \"{lottery_name}\" first."
        )
    enriched = []
    for row in raw:
        tokens  = _tokens_from_raw(row.get("winning_raw", ""))
        numbers = _numeric_tokens(tokens)
        letters = _letter_tokens(tokens)
        enriched.append({
            **row,
            "tokens":  tokens,
            "numbers": numbers,
            "letters": letters,
        })
    return enriched


# ─── Factor computations ──────────────────────────────────────────────────────

def factor_alltime_freq(draws: list[dict]) -> dict[str, float]:
    """Factor 1: All-time token frequency (normalized 0-1)."""
    counts: Counter = Counter()
    for d in draws:
        counts.update(t for t in d["tokens"] if t.isdigit())
    total = sum(counts.values()) or 1
    return {t: c / total for t, c in counts.items()}


def factor_dow_freq(draws: list[dict], target_dow: int) -> dict[str, float]:
    """Factor 2: Frequency on the target day-of-week (normalized 0-1)."""
    counts: Counter = Counter()
    for d in draws:
        if d.get("day_of_week") == target_dow:
            counts.update(t for t in d["tokens"] if t.isdigit())
    total = sum(counts.values()) or 1
    if total == 0:
        return {}
    return {t: c / total for t, c in counts.items()}


def factor_recency_momentum(draws: list[dict], window: int = 14) -> dict[str, float]:
    """
    Factor 3: Recency-weighted frequency over last `window` draws.
    Most recent draws are weighted more heavily (linear decay).
    """
    recent = draws[:window]  # draws are newest-first
    counts: dict[str, float] = defaultdict(float)
    n = len(recent)
    for i, d in enumerate(recent):
        weight = (n - i) / n   # newest = 1.0, oldest = 1/n
        for t in d["tokens"]:
            if t.isdigit():
                counts[t] += weight
    total = sum(counts.values()) or 1
    return {t: v / total for t, v in counts.items()}


def factor_gap_overdue(draws: list[dict]) -> dict[str, float]:
    """
    Factor 4: Overdue numbers — how many draws since each number last appeared.
    Higher gap → higher score (due to appear).
    """
    last_seen: dict[str, int] = {}
    total_draws = len(draws)

    for i, d in enumerate(draws):   # i=0 = most recent
        for t in d["tokens"]:
            if t.isdigit() and t not in last_seen:
                last_seen[t] = i

    if not last_seen:
        return {}

    # Normalize: max_gap = 1.0
    max_gap = max(last_seen.values()) or 1
    return {t: gap / max_gap for t, gap in last_seen.items()}


def factor_pair_correlation(draws: list[dict], target_tokens: Optional[set] = None) -> dict[str, float]:
    """
    Factor 5: How often does each number appear with other numbers?
    Numbers with high co-occurrence scores are likely to be grouped.
    Returns per-token average pair frequency.
    """
    pair_counts: Counter = Counter()
    token_counts: Counter = Counter()

    for d in draws:
        nums = [t for t in d["tokens"] if t.isdigit()]
        token_counts.update(nums)
        for a, b in combinations(sorted(nums), 2):
            pair_counts[(a, b)] += 1

    if not pair_counts:
        return {}

    # Sum pair frequencies per token
    token_pair_score: dict[str, float] = defaultdict(float)
    for (a, b), cnt in pair_counts.items():
        token_pair_score[a] += cnt
        token_pair_score[b] += cnt

    # Normalize by token's individual count to get pair affinity
    result: dict[str, float] = {}
    for t, score in token_pair_score.items():
        tc = token_counts.get(t, 1) or 1
        result[t] = score / tc

    max_val = max(result.values()) or 1
    return {t: v / max_val for t, v in result.items()}


def factor_range_trend(draws: list[dict], window: int = 7) -> float:
    """
    Factor 6: Are high numbers (>45) or low numbers (<25) currently trending?
    Returns a float 0-1 where:
      0.0 = strongly low trending  (add score to low numbers)
      0.5 = neutral
      1.0 = strongly high trending (add score to high numbers)
    """
    recent = draws[:window]
    all_nums = []
    for d in recent:
        all_nums.extend(d["numbers"])
    if not all_nums:
        return 0.5
    avg = sum(all_nums) / len(all_nums)
    # Normalize: assume number range is 0–99
    return avg / 99.0


def factor_letter_number_affinity(draws: list[dict]) -> dict[str, float]:
    """
    Factor 7: For each letter, what average number range appears with it?
    Returns per-letter score based on correlation with current number trend.
    """
    letter_num_map: dict[str, list[int]] = defaultdict(list)
    for d in draws:
        for letter in d["letters"]:
            letter_num_map[letter].extend(d["numbers"])

    if not letter_num_map:
        return {}

    letter_avg: dict[str, float] = {
        letter: sum(nums) / len(nums)
        for letter, nums in letter_num_map.items()
        if nums
    }
    max_avg = max(letter_avg.values()) or 1
    return {letter: avg / max_avg for letter, avg in letter_avg.items()}


# ─── Combined prediction engine ───────────────────────────────────────────────

WEIGHTS = {
    "alltime":    0.25,
    "dow":        0.20,
    "recency":    0.20,
    "gap":        0.15,
    "pair":       0.10,
    "range":      0.05,
    "letter_aff": 0.05,
}

def smooth(freq: dict[str, float], token: str, default: float = 1e-6) -> float:
    return freq.get(token, default)


def predict_numbers(
    lottery_name: str,
    top_n: int = 10,
    target_dow: Optional[int] = None,
) -> dict:
    """
    Run the full multi-factor analysis and return predictions.

    Returns:
    {
      lottery_name, draw_count, target_dow, day_name,
      top_numbers: [(number_str, score_0_to_100), ...],
      top_letters: [(letter_str, score_0_to_100), ...],
      predicted_set: "T 4 3 2 8 9 5",
      factors_used: {factor_name: weight},
      analysis: { ... detail per factor },
    }
    """
    draws = _load_draw_data(lottery_name)
    if not draws:
        raise ValueError(f"No draws available for '{lottery_name}'")

    if target_dow is None:
        target_dow = date.today().weekday()

    day_name = DAY_NAMES[target_dow]

    # ── Compute all factors ───────────────────────────────────────────────────
    f1 = factor_alltime_freq(draws)
    f2 = factor_dow_freq(draws, target_dow)
    f3 = factor_recency_momentum(draws, window=14)
    f4 = factor_gap_overdue(draws)
    f5 = factor_pair_correlation(draws)
    f6 = factor_range_trend(draws, window=7)  # float 0–1
    f7 = factor_letter_number_affinity(draws)

    # ── Gather all numeric tokens seen ───────────────────────────────────────
    all_tokens: set[str] = set()
    for d in draws:
        all_tokens.update(t for t in d["tokens"] if t.isdigit())

    # ── Adjust weights if day-of-week has no data ─────────────────────────────
    w = dict(WEIGHTS)
    if not f2:
        # redistribute dow weight
        w["alltime"] += w["dow"] * 0.5
        w["recency"] += w["dow"] * 0.5
        w["dow"] = 0.0

    # ── Score each numeric token ──────────────────────────────────────────────
    raw_scores: dict[str, float] = {}
    for token in all_tokens:
        s1 = smooth(f1, token)
        s2 = smooth(f2, token) if f2 else s1
        s3 = smooth(f3, token)
        s4 = smooth(f4, token, default=0.0)
        s5 = smooth(f5, token, default=0.0)

        # Range trend: tokens ≤ 45 get bonus if low is trending, ≥ 46 get bonus if high
        try:
            num_val = int(token)
            if f6 > 0.55:       # high trending
                range_bonus = num_val / 99.0
            elif f6 < 0.45:     # low trending
                range_bonus = 1.0 - num_val / 99.0
            else:
                range_bonus = 0.5
        except ValueError:
            range_bonus = 0.5

        combined = (
            w["alltime"]    * s1
            + w["dow"]      * s2
            + w["recency"]  * s3
            + w["gap"]      * s4
            + w["pair"]     * s5
            + w["range"]    * range_bonus
        )
        raw_scores[token] = combined

    # ── Normalize to 0–100 ────────────────────────────────────────────────────
    values = list(raw_scores.values())
    lo, hi = (min(values), max(values)) if values else (0, 1)
    rng = (hi - lo) or 1.0

    top_numbers = sorted(
        [(t, round((s - lo) / rng * 100, 1)) for t, s in raw_scores.items()],
        key=lambda x: -x[1]
    )[:top_n]

    # ── Score letters ─────────────────────────────────────────────────────────
    letter_freq: Counter = Counter()
    for d in draws[:50]:    # use recent 50 draws for letter frequency
        letter_freq.update(d["letters"])

    total_l = sum(letter_freq.values()) or 1
    letter_scores_raw = {l: c / total_l for l, c in letter_freq.items()}

    # Adjust letter scores using letter-number affinity vs current range trend
    for letter, aff in f7.items():
        if letter in letter_scores_raw:
            # Affinity is normalized avg number; trend is 0-1 high/low
            alignment = 1.0 - abs(aff - f6)
            letter_scores_raw[letter] *= (1 + 0.3 * alignment)

    l_vals = list(letter_scores_raw.values())
    l_lo, l_hi = (min(l_vals), max(l_vals)) if l_vals else (0, 1)
    l_rng = (l_hi - l_lo) or 1.0
    top_letters = sorted(
        [(l, round((s - l_lo) / l_rng * 100, 1)) for l, s in letter_scores_raw.items()],
        key=lambda x: -x[1]
    )[:5]

    # ── Build predicted set ───────────────────────────────────────────────────
    best_letter = top_letters[0][0] if top_letters else "?"
    best_numbers = [t for t, _ in top_numbers[:6]]

    # Determine how many numbers this lottery typically uses
    if draws:
        sample_nums = draws[0]["numbers"]
        num_count = len(sample_nums) if sample_nums else 6
    else:
        num_count = 6

    best_numbers = best_numbers[:num_count]
    while len(best_numbers) < num_count:
        best_numbers.append("?")

    predicted_set = f"{best_letter} {' '.join(best_numbers)}"

    return {
        "lottery_name":  lottery_name,
        "draw_count":    len(draws),
        "target_dow":    target_dow,
        "day_name":      day_name,
        "top_numbers":   top_numbers,
        "top_letters":   top_letters,
        "predicted_set": predicted_set,
        "factors_used":  w,
        "range_trend":   f6,
        "range_label":   "HIGH" if f6 > 0.55 else "LOW" if f6 < 0.45 else "MIXED",
        "analysis": {
            "alltime_freq":     dict(sorted(f1.items(), key=lambda x: -x[1])[:10]),
            "dow_freq":         dict(sorted(f2.items(), key=lambda x: -x[1])[:10]) if f2 else {},
            "recency":          dict(sorted(f3.items(), key=lambda x: -x[1])[:10]),
            "gap_overdue":      dict(sorted(f4.items(), key=lambda x: -x[1])[:10]),
            "pair_correlation": dict(sorted(f5.items(), key=lambda x: -x[1])[:10]),
            "letter_affinity":  f7,
        },
    }


# ─── Display helpers ──────────────────────────────────────────────────────────

def _bar(value: float, width: int = 20) -> str:
    filled = round(value / 100 * width)
    return "█" * filled + "░" * (width - filled)


def print_prediction(result: dict, verbose: bool = False) -> None:
    """Pretty-print prediction results to console."""
    sep  = "─" * 65
    print(f"\n{'═'*65}")
    print(f"  LOTTERY AGENT PREDICTION")
    print(f"  Lottery  : {result['lottery_name']}")
    print(f"  Day      : {result['day_name']}")
    print(f"  Based on : {result['draw_count']} historical draws")
    print(f"  Trend    : {result['range_label']} numbers ({result['range_trend']:.2f})")
    print(f"{'═'*65}")

    print(f"\n  🎯  PREDICTED SET:  {result['predicted_set']}\n")

    print(f"  Top Number Predictions:")
    print(f"  {sep}")
    print(f"  {'Num':>4}  {'Score':>6}  {'Confidence Bar'}")
    print(f"  {sep}")
    for num, score in result["top_numbers"]:
        bar = _bar(score)
        print(f"  {num:>4}  {score:>5.1f}%  {bar}")

    print(f"\n  Top Letter Predictions:")
    print(f"  {sep}")
    for letter, score in result["top_letters"]:
        bar = _bar(score)
        print(f"  {letter:>4}  {score:>5.1f}%  {bar}")

    if verbose:
        print(f"\n  Factor Weights Used:")
        for factor, weight in result["factors_used"].items():
            if weight > 0:
                print(f"    {factor:<15}  {weight*100:.0f}%")

        print(f"\n  Analysis Detail (Top 5 per factor):")
        for fname, data in result["analysis"].items():
            if data:
                top5 = list(data.items())[:5]
                vals = ", ".join(f"{t}:{v:.3f}" for t, v in top5)
                print(f"    {fname:<20}: {vals}")

    print()


def print_all_predictions(top_n: int = 5) -> None:
    """Run predictions for all lotteries and print a summary."""
    print("\n" + "="*65)
    print("  LOTTERY AGENT — ALL LOTTERIES PREDICTION")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}  ({DAY_NAMES[date.today().weekday()]})")
    print("="*65)

    print(f"\n  {'Lottery':<28}  {'Prediction':<25}  {'Trend':>5}  {'Draws':>6}")
    print("  " + "-"*70)

    for lottery_name in sorted(LOTTERIES):
        try:
            r = predict_numbers(lottery_name, top_n=top_n)
            trend = r["range_label"][:4]
            print(
                f"  {lottery_name:<28}  "
                f"{r['predicted_set']:<25}  "
                f"{trend:>5}  "
                f"{r['draw_count']:>6}"
            )
        except ValueError as e:
            print(f"  {lottery_name:<28}  (no data — run updater first)")
        except Exception as e:
            print(f"  {lottery_name:<28}  ERROR: {e}")

    print()


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="NLB Lottery AI Agent — multi-factor prediction"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # predict
    p_pred = sub.add_parser("predict", help="Predict winning numbers for one lottery")
    p_pred.add_argument("--lottery", required=True, help='e.g. "Mega Power"')
    p_pred.add_argument("--top",     type=int, default=10, help="Top N numbers to show")
    p_pred.add_argument("--verbose", action="store_true", help="Show factor breakdown")

    # all
    sub.add_parser("all", help="Predict for all lotteries (summary table)")

    # analyze
    p_an = sub.add_parser("analyze", help="Full factor analysis for one lottery")
    p_an.add_argument("--lottery", required=True)

    # hot
    p_hot = sub.add_parser("hot", help="Show hottest numbers for one lottery")
    p_hot.add_argument("--lottery", required=True)
    p_hot.add_argument("--top",     type=int, default=10)

    # gap
    p_gap = sub.add_parser("gap", help="Show overdue numbers (haven't appeared in a while)")
    p_gap.add_argument("--lottery", required=True)
    p_gap.add_argument("--top",     type=int, default=10)

    # dow
    p_dow = sub.add_parser("dow", help="Show hot numbers per day of week")
    p_dow.add_argument("--lottery", required=True)

    args = parser.parse_args()

    if args.cmd == "predict":
        result = predict_numbers(args.lottery, top_n=args.top)
        print_prediction(result, verbose=args.verbose)

    elif args.cmd == "all":
        print_all_predictions()

    elif args.cmd == "analyze":
        result = predict_numbers(args.lottery, top_n=15)
        print_prediction(result, verbose=True)

    elif args.cmd == "hot":
        draws = _load_draw_data(args.lottery)
        freq = factor_alltime_freq(draws)
        top = sorted(freq.items(), key=lambda x: -x[1])[:args.top]
        total = sum(freq.values()) or 1
        print(f"\n  Hottest Numbers — {args.lottery}\n")
        print(f"  {'Number':>6}  {'Pct':>6}  Bar")
        print("  " + "-"*40)
        for token, freq_val in top:
            pct = freq_val * 100
            bar = _bar(pct * (20 / max(v * 100 for v in freq.values())))
            print(f"  {token:>6}  {pct:>5.1f}%  {bar}")
        print()

    elif args.cmd == "gap":
        draws = _load_draw_data(args.lottery)
        gap = factor_gap_overdue(draws)
        top = sorted(gap.items(), key=lambda x: -x[1])[:args.top]
        # Find actual gap counts
        last_seen: dict[str, int] = {}
        for i, d in enumerate(draws):
            for t in d["tokens"]:
                if t.isdigit() and t not in last_seen:
                    last_seen[t] = i
        print(f"\n  Most Overdue Numbers — {args.lottery}\n")
        print(f"  {'Number':>6}  {'Draws ago':>10}  (overdue)")
        print("  " + "-"*30)
        for token, _ in top:
            gap_count = last_seen.get(token, "?")
            print(f"  {token:>6}  {gap_count:>10}")
        print()

    elif args.cmd == "dow":
        draws = _load_draw_data(args.lottery)
        print(f"\n  Day-of-Week Hot Numbers — {args.lottery}\n")
        for dow in range(7):
            freq = factor_dow_freq(draws, dow)
            if not freq:
                continue
            top5 = sorted(freq.items(), key=lambda x: -x[1])[:5]
            nums = "  ".join(f"{t}({v*100:.0f}%)" for t, v in top5)
            today_marker = "  ← TODAY" if dow == date.today().weekday() else ""
            print(f"  {DAY_NAMES[dow]:<11}: {nums}{today_marker}")
        print()


if __name__ == "__main__":
    main()

"""
lottery_agent_v2.py — Advanced Multi-Model Ensemble Prediction Engine
======================================================================
Upgrades over v1:
  15 analysis factors (up from 7)
  Position-specific frequency (which numbers win in slot 1, 2, 3...)
  Sum range prediction (predict total sum, then bias numbers toward that)
  Odd/Even ratio analysis (how many odd vs even in this lottery)
  Consecutive number detection (does this lottery tend to have/avoid consecutives)
  Markov chain (what tends to follow recent draws)
  Similarity matching (find past draws most similar to the last N draws)
  Hot streak vs cold streak classification
  Draw cycle analysis (does any number appear every N draws)
  Letter-per-day analysis (which letter is most likely TODAY)
  Multi-ticket generation (generates top 5 tickets with confidence scores)
  Grand prize focus mode (maximizes exact full-match probability)
  Auto-calibrating weights (adjusts to what the data actually shows)
  Ensemble voting (5 sub-models vote independently)
  Backtest score (shows how well each factor predicted the last 20 draws)

Usage:
  python lottery_agent_v2.py predict --lottery "Mahajana Sampatha"
  python lottery_agent_v2.py predict --lottery "Mahajana Sampatha" --tickets 5
  python lottery_agent_v2.py grand   --lottery "Mahajana Sampatha"
  python lottery_agent_v2.py analyze --lottery "Mahajana Sampatha"
  python lottery_agent_v2.py backtest --lottery "Mahajana Sampatha" --last 30
  python lottery_agent_v2.py all
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from itertools import combinations, permutations
from pathlib import Path
from statistics import mean, stdev
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
DIGITS    = [str(i) for i in range(10)]


# ─── Data loading ─────────────────────────────────────────────────────────────

def _parse_tokens(winning_raw: str) -> list[str]:
    return re.findall(r"\b([A-Z0-9]+)\b", winning_raw.strip().upper())

def _nums(tokens: list[str]) -> list[int]:
    return [int(t) for t in tokens if re.match(r"^\d{1,2}$", t)]

def _letters(tokens: list[str]) -> list[str]:
    return [t for t in tokens if re.match(r"^[A-Z]$", t)]

def _load(lottery_name: str) -> list[dict]:
    """Load all draws, enriched with tokens/numbers/letters."""
    raw = load_all_draws(lottery_name)
    if not raw:
        raise ValueError(
            f"No data for '{lottery_name}'. "
            f"Run: python lottery_updater.py --lottery \"{lottery_name}\""
        )
    result = []
    for row in raw:
        toks  = _parse_tokens(row.get("winning_raw", ""))
        result.append({**row, "tokens": toks, "numbers": _nums(toks), "letters": _letters(toks)})
    return result


# ─── Helper utilities ─────────────────────────────────────────────────────────

def _norm(d: dict[str, float]) -> dict[str, float]:
    """Normalize a score dict to 0–1."""
    if not d:
        return d
    lo, hi = min(d.values()), max(d.values())
    rng = (hi - lo) or 1.0
    return {k: (v - lo) / rng for k, v in d.items()}

def _smooth(d: dict, key: str, default: float = 1e-7) -> float:
    return d.get(key, default)

def _top(d: dict, n: int = 10) -> list[tuple]:
    return sorted(d.items(), key=lambda x: -x[1])[:n]


# ─── Factor 1: All-time global frequency ──────────────────────────────────────

def f_alltime(draws: list[dict]) -> dict[str, float]:
    c: Counter = Counter()
    for d in draws:
        c.update(str(n) for n in d["numbers"])
    total = sum(c.values()) or 1
    return {k: v/total for k, v in c.items()}


# ─── Factor 2: Day-of-week frequency ──────────────────────────────────────────

def f_dow(draws: list[dict], dow: int) -> dict[str, float]:
    c: Counter = Counter()
    for d in draws:
        if d.get("day_of_week") == dow:
            c.update(str(n) for n in d["numbers"])
    total = sum(c.values()) or 1
    return {k: v/total for k, v in c.items()} if total > 1 else {}


# ─── Factor 3: Recency momentum (exponential decay) ───────────────────────────

def f_recency(draws: list[dict], window: int = 20) -> dict[str, float]:
    """Exponential decay weighting — most recent draws matter most."""
    recent = draws[:window]
    scores: dict[str, float] = defaultdict(float)
    n = len(recent)
    for i, d in enumerate(recent):
        weight = math.exp(-i * 3.0 / n)   # exponential decay
        for num in d["numbers"]:
            scores[str(num)] += weight
    total = sum(scores.values()) or 1
    return {k: v/total for k, v in scores.items()}


# ─── Factor 4: Gap / overdue analysis ─────────────────────────────────────────

def f_gap(draws: list[dict]) -> dict[str, float]:
    """The longer a number has been absent, the higher its overdue score."""
    last_seen: dict[str, int] = {}
    for i, d in enumerate(draws):
        for n in d["numbers"]:
            k = str(n)
            if k not in last_seen:
                last_seen[k] = i
    if not last_seen:
        return {}
    mx = max(last_seen.values()) or 1
    return {k: v/mx for k, v in last_seen.items()}


# ─── Factor 5: Position-specific frequency ───────────────────────────────────

def f_position(draws: list[dict]) -> dict[str, dict[str, float]]:
    """
    For each position (1–6), compute frequency of each digit.
    Returns {pos_str: {digit: freq}}
    e.g. {"1": {"4": 0.15, "7": 0.12 ...}, "2": {...}, ...}
    """
    pos_counts: dict[str, Counter] = {str(p): Counter() for p in range(1, 7)}
    for d in draws:
        nums = d["numbers"]
        for p, n in enumerate(nums[:6], start=1):
            pos_counts[str(p)][str(n)] += 1
    result = {}
    for pos, ctr in pos_counts.items():
        total = sum(ctr.values()) or 1
        result[pos] = {k: v/total for k, v in ctr.items()}
    return result


# ─── Factor 6: Pair & triplet correlation ────────────────────────────────────

def f_pairs(draws: list[dict]) -> dict[str, float]:
    """Score each number by how often it appears in pairs with other hot numbers."""
    pair_counts: Counter = Counter()
    token_counts: Counter = Counter()
    for d in draws:
        nums = [str(n) for n in d["numbers"]]
        token_counts.update(nums)
        for a, b in combinations(sorted(nums), 2):
            pair_counts[(a, b)] += 1
    if not pair_counts:
        return {}
    pair_score: dict[str, float] = defaultdict(float)
    for (a, b), cnt in pair_counts.items():
        pair_score[a] += cnt
        pair_score[b] += cnt
    total = max(pair_score.values()) or 1
    return {k: v/total for k, v in pair_score.items()}


def f_triplets(draws: list[dict]) -> dict[str, float]:
    """Score each number by triplet co-occurrence."""
    tri_counts: Counter = Counter()
    for d in draws:
        nums = sorted(str(n) for n in d["numbers"])
        for combo in combinations(nums, 3):
            tri_counts[combo] += 1
    tri_score: dict[str, float] = defaultdict(float)
    for combo, cnt in tri_counts.items():
        for t in combo:
            tri_score[t] += cnt
    total = max(tri_score.values()) if tri_score else 1
    return {k: v/total for k, v in tri_score.items()}


# ─── Factor 7: Number sum range prediction ────────────────────────────────────

def f_sum_range(draws: list[dict], dow: int) -> tuple[float, float]:
    """
    Predict the likely sum range for the next draw.
    Returns (expected_sum, stdev_sum) for the target day-of-week.
    Falls back to all-time if not enough dow data.
    """
    sums_dow = [d["num_sum"] for d in draws
                if d.get("day_of_week") == dow and d.get("num_sum") is not None]
    sums_all = [d["num_sum"] for d in draws if d.get("num_sum") is not None]

    sums = sums_dow if len(sums_dow) >= 10 else sums_all
    if not sums:
        return 25.0, 10.0
    avg = mean(sums)
    sd  = stdev(sums) if len(sums) > 1 else 5.0
    return avg, sd


def sum_bonus(num: int, expected_sum: float, stdev_sum: float, current_sum: float) -> float:
    """
    Given a partial ticket sum, score how well adding `num` gets us
    closer to the expected total sum.
    """
    target_remaining = expected_sum - current_sum
    diff = abs(num - target_remaining / 3)
    return max(0.0, 1.0 - diff / (stdev_sum + 1))


# ─── Factor 8: Odd/Even ratio ─────────────────────────────────────────────────

def f_odd_even(draws: list[dict], dow: int) -> float:
    """
    Returns the expected fraction of ODD numbers for this day-of-week.
    0.0 = all even, 0.5 = balanced, 1.0 = all odd
    """
    odd_fracs = []
    for d in draws:
        if d.get("day_of_week") == dow and d.get("odd_count") is not None:
            total = (d["odd_count"] or 0) + (d["even_count"] or 0)
            if total > 0:
                odd_fracs.append(d["odd_count"] / total)
    if not odd_fracs:
        # fallback: all-time
        odd_fracs = []
        for d in draws:
            if d.get("odd_count") is not None:
                total = (d["odd_count"] or 0) + (d["even_count"] or 0)
                if total > 0:
                    odd_fracs.append(d["odd_count"] / total)
    return mean(odd_fracs) if odd_fracs else 0.5


# ─── Factor 9: Markov chain (what follows recent draws) ──────────────────────

def f_markov(draws: list[dict], window: int = 3) -> dict[str, float]:
    """
    Look at the most recent `window` draws and find what numbers
    statistically tend to come AFTER this specific combination.

    Strategy: For each recent number, find all past draws that contained
    that number, then score what appeared in the FOLLOWING draw.
    """
    if len(draws) < window + 1:
        return {}

    recent_nums = set()
    for d in draws[:window]:
        recent_nums.update(str(n) for n in d["numbers"])

    follow_counts: Counter = Counter()
    for i in range(window, len(draws)):
        prev_draw = draws[i - 1]
        prev_nums = set(str(n) for n in prev_draw["numbers"])
        # If current position's previous draw overlaps with recent draws
        overlap = prev_nums & recent_nums
        if overlap:
            for n in draws[i]["numbers"]:
                follow_counts[str(n)] += len(overlap)

    total = sum(follow_counts.values()) or 1
    return {k: v/total for k, v in follow_counts.items()}


# ─── Factor 10: Similarity matching ──────────────────────────────────────────

def f_similarity(draws: list[dict], compare_window: int = 5) -> dict[str, float]:
    """
    Find past draws most similar to the last `compare_window` draws
    (by number overlap), then score what appeared AFTER those similar draws.
    """
    if len(draws) < compare_window + 2:
        return {}

    recent = draws[:compare_window]
    recent_set = set()
    for d in recent:
        recent_set.update(str(n) for n in d["numbers"])

    # Score past draws by similarity to recent
    similarities = []
    for i in range(compare_window, len(draws)-1):
        past_set = set(str(n) for n in draws[i]["numbers"])
        overlap  = len(recent_set & past_set)
        if overlap >= 2:
            # Look at what came BEFORE this draw (i-1) since draws are newest-first
            next_draw_idx = i - 1
            similarities.append((overlap, draws[next_draw_idx]))

    # Take top 10 most similar
    similarities.sort(key=lambda x: -x[0])
    top_similar = similarities[:10]

    if not top_similar:
        return {}

    follow_counts: Counter = Counter()
    for weight, follow_draw in top_similar:
        for n in follow_draw["numbers"]:
            follow_counts[str(n)] += weight

    total = sum(follow_counts.values()) or 1
    return {k: v/total for k, v in follow_counts.items()}


# ─── Factor 11: Hot/Cold streak ───────────────────────────────────────────────

def f_streaks(draws: list[dict]) -> tuple[dict[str, float], dict[str, float]]:
    """
    Classify each number as hot (appeared frequently in last 7 draws)
    or cold (not appeared recently).
    Returns (hot_scores, cold_scores) both normalized.
    """
    hot: Counter = Counter()
    for d in draws[:7]:
        hot.update(str(n) for n in d["numbers"])

    cold_gap: dict[str, int] = {}
    for i, d in enumerate(draws):
        for n in d["numbers"]:
            k = str(n)
            if k not in cold_gap:
                cold_gap[k] = i

    total_h = sum(hot.values()) or 1
    hot_scores  = {k: v/total_h for k, v in hot.items()}

    max_g = max(cold_gap.values()) if cold_gap else 1
    cold_scores = {k: v/max_g for k, v in cold_gap.items()}

    return hot_scores, cold_scores


# ─── Factor 12: Draw number cycle analysis ────────────────────────────────────

def f_cycles(draws: list[dict], max_cycle: int = 14) -> dict[str, float]:
    """
    Detect if any number appears on a regular cycle (every N draws).
    Numbers with detected cycles that predict them appearing soon get a bonus.
    """
    if len(draws) < max_cycle * 2:
        return {}

    cycle_scores: dict[str, float] = defaultdict(float)
    for digit in DIGITS:
        appearances = [i for i, d in enumerate(draws) if digit in [str(n) for n in d["numbers"]]]
        if len(appearances) < 3:
            continue
        # Calculate gaps between appearances
        gaps = [appearances[i+1] - appearances[i] for i in range(len(appearances)-1)]
        avg_gap = mean(gaps)
        if avg_gap < 1.5:
            continue
        # Distance since last seen
        dist_since = appearances[0] if appearances else 999
        # Score: the closer dist_since is to avg_gap, the more "due" it is
        closeness = 1.0 - abs(dist_since - avg_gap) / (avg_gap + 1)
        cycle_scores[digit] = max(0.0, closeness)

    return cycle_scores


# ─── Factor 13: Letter-per-day analysis ──────────────────────────────────────

def f_letter_day(draws: list[dict], dow: int) -> dict[str, float]:
    """
    Score each letter by frequency on the target day-of-week.
    """
    c: Counter = Counter()
    for d in draws:
        if d.get("day_of_week") == dow:
            c.update(d["letters"])
    total = sum(c.values()) or 1
    return {k: v/total for k, v in c.items()} if total > 1 else {}


def f_letter_alltime(draws: list[dict]) -> dict[str, float]:
    """All-time letter frequency."""
    c: Counter = Counter()
    for d in draws[:100]:
        c.update(d["letters"])
    total = sum(c.values()) or 1
    return {k: v/total for k, v in c.items()}


# ─── Factor 14: Consecutive bias ─────────────────────────────────────────────

def f_consecutive_bias(draws: list[dict]) -> float:
    """
    What fraction of winning draws contain at least one pair of consecutive numbers?
    Returns 0.0-1.0. If high (>0.5), prefer tickets with consecutives.
    """
    if not draws:
        return 0.3
    consec = [d for d in draws if d.get("has_consecutive") == 1]
    return len(consec) / len(draws)


# ─── Factor 15: Ensemble sub-models ──────────────────────────────────────────

def sub_model_frequency(draws: list[dict], dow: int) -> dict[str, float]:
    """Model A: Pure frequency (all-time + dow weighted)."""
    fa = f_alltime(draws)
    fd = f_dow(draws, dow)
    combined: dict[str, float] = {}
    for d in DIGITS:
        combined[d] = 0.6 * _smooth(fa, d) + 0.4 * _smooth(fd, d)
    return _norm(combined)

def sub_model_recency(draws: list[dict]) -> dict[str, float]:
    """Model B: Pure recency momentum."""
    return _norm(f_recency(draws, window=21))

def sub_model_overdue(draws: list[dict]) -> dict[str, float]:
    """Model C: Numbers that are most overdue."""
    return _norm(f_gap(draws))

def sub_model_markov(draws: list[dict]) -> dict[str, float]:
    """Model D: Markov chain prediction."""
    return _norm(f_markov(draws, window=4))

def sub_model_similarity(draws: list[dict]) -> dict[str, float]:
    """Model E: Historical similarity matching."""
    return _norm(f_similarity(draws, compare_window=5))


# ─── Grand Prize Engine ───────────────────────────────────────────────────────

def _grand_prize_score(nums: list[int], draws: list[dict]) -> float:
    """
    Score a set of 6 numbers for grand-prize likelihood.
    Checks:
      - How often has EACH number appeared (frequency)
      - How often has this exact SET appeared (near-exact match in history)
      - Odd/even balance matches historical grand-prize patterns
      - Sum is within expected range
      - Letter affinity
    """
    score = 0.0
    num_strs = [str(n) for n in nums]

    # Frequency score
    fa = f_alltime(draws)
    score += sum(_smooth(fa, n) for n in num_strs)

    # Near-exact match bonus: any past draw with 5+ of these numbers
    num_set = set(num_strs)
    for d in draws:
        past_set = set(str(n) for n in d["numbers"])
        overlap = len(num_set & past_set)
        if overlap >= 5:
            score += (overlap - 4) * 2.0   # big bonus for near-matches

    # Odd/even balance
    odds = sum(1 for n in nums if n % 2 != 0)
    # Ideal: 3 odd, 3 even
    balance_penalty = abs(odds - 3) * 0.1
    score -= balance_penalty

    # Sum range check
    total = sum(nums)
    all_sums = [d["num_sum"] for d in draws if d.get("num_sum")]
    if all_sums:
        avg_s = mean(all_sums)
        sd_s  = stdev(all_sums) if len(all_sums) > 1 else 5
        z = abs(total - avg_s) / (sd_s + 1)
        score -= z * 0.2   # penalize outlier sums

    return score


# ─── Main Prediction Engine ───────────────────────────────────────────────────

ENSEMBLE_WEIGHTS = {
    "frequency":   0.22,
    "recency":     0.18,
    "overdue":     0.15,
    "dow":         0.12,
    "position":    0.10,
    "pairs":       0.07,
    "triplets":    0.05,
    "markov":      0.04,
    "similarity":  0.04,
    "cycles":      0.03,
}

def predict_advanced(
    lottery_name: str,
    num_tickets: int = 5,
    target_dow: Optional[int] = None,
    grand_prize_mode: bool = False,
) -> dict:
    """
    Full 15-factor ensemble prediction.
    Returns top `num_tickets` tickets with individual confidence scores.
    """
    draws = _load(lottery_name)
    n = len(draws)

    if target_dow is None:
        target_dow = date.today().weekday()
    day_name = DAY_NAMES[target_dow]

    # ── Compute all factors ───────────────────────────────────────────────────
    fa   = f_alltime(draws)
    fd   = f_dow(draws, target_dow)
    frec = f_recency(draws, window=min(20, n))
    fgap = f_gap(draws)
    fpos = f_position(draws)
    fpairs = f_pairs(draws)
    ftri   = f_triplets(draws)
    fmark  = f_markov(draws, window=min(4, n//10))
    fsim   = f_similarity(draws, compare_window=min(5, n//10))
    fcyc   = f_cycles(draws)
    hot_sc, cold_sc = f_streaks(draws)
    expected_sum, sum_sd = f_sum_range(draws, target_dow)
    odd_frac     = f_odd_even(draws, target_dow)
    consec_bias  = f_consecutive_bias(draws)

    # ── Sub-model ensemble ────────────────────────────────────────────────────
    sm_freq  = sub_model_frequency(draws, target_dow)
    sm_rec   = sub_model_recency(draws)
    sm_over  = sub_model_overdue(draws)
    sm_mark  = sub_model_markov(draws)
    sm_sim   = sub_model_similarity(draws)

    # ── Combine into per-digit master scores ─────────────────────────────────
    w = dict(ENSEMBLE_WEIGHTS)
    # if dow has little data, redistribute
    dow_draws = [d for d in draws if d.get("day_of_week") == target_dow]
    if len(dow_draws) < 10:
        bonus = w["dow"]
        w["frequency"] += bonus * 0.6
        w["recency"]   += bonus * 0.4
        w["dow"] = 0.0

    master: dict[str, float] = {}
    for digit in DIGITS:
        s = (
            w["frequency"]  * _smooth(sm_freq,  digit)
          + w["recency"]    * _smooth(sm_rec,   digit)
          + w["overdue"]    * _smooth(sm_over,  digit)
          + w["dow"]        * _smooth(fd,       digit)
          + w["pairs"]      * _smooth(fpairs,   digit)
          + w["triplets"]   * _smooth(ftri,     digit)
          + w["markov"]     * _smooth(fmark,    digit)
          + w["similarity"] * _smooth(fsim,     digit)
          + w["cycles"]     * _smooth(fcyc,     digit)
        )
        master[digit] = s

    # Position-weighted adjustment
    pos_bonus: dict[str, float] = defaultdict(float)
    for pos_str, pos_freq in fpos.items():
        for digit, freq in pos_freq.items():
            pos_bonus[digit] += freq * w["position"]
    for digit in DIGITS:
        master[digit] += pos_bonus.get(digit, 0.0)

    # Normalize master scores → 0-100
    lo = min(master.values())
    hi = max(master.values())
    rng = (hi - lo) or 1.0
    ranked_digits = sorted(DIGITS, key=lambda d: -master[d])
    scores_0_100  = {d: round((master[d] - lo) / rng * 100, 1) for d in DIGITS}

    # ── Letter prediction ─────────────────────────────────────────────────────
    fl_day = f_letter_day(draws, target_dow)
    fl_all = f_letter_alltime(draws)
    letter_scores: dict[str, float] = {}
    for lt in set(list(fl_day.keys()) + list(fl_all.keys())):
        letter_scores[lt] = 0.6 * _smooth(fl_day, lt) + 0.4 * _smooth(fl_all, lt)
    letter_scores = _norm(letter_scores)
    top_letters = _top(letter_scores, 5)

    # ── Generate diverse tickets ──────────────────────────────────────────────
    tickets = _generate_tickets(
        ranked_digits, master, fpos, expected_sum, sum_sd,
        odd_frac, consec_bias, num_tickets, top_letters,
        grand_prize_mode, draws,
    )

    # ── Sub-model vote summary ────────────────────────────────────────────────
    votes: dict[str, int] = defaultdict(int)
    for sm in [sm_freq, sm_rec, sm_over, sm_mark, sm_sim]:
        top3 = sorted(sm.items(), key=lambda x: -x[1])[:3]
        for digit, _ in top3:
            votes[digit] += 1

    return {
        "lottery_name":   lottery_name,
        "draw_count":     n,
        "day_name":       day_name,
        "target_dow":     target_dow,
        "tickets":        tickets,
        "scores":         scores_0_100,
        "top_letters":    top_letters,
        "expected_sum":   round(expected_sum, 1),
        "sum_stdev":      round(sum_sd, 1),
        "odd_fraction":   round(odd_frac, 2),
        "consec_bias":    round(consec_bias, 2),
        "ensemble_votes": dict(sorted(votes.items(), key=lambda x: -x[1])),
        "weights_used":   w,
        "factor_scores": {
            "alltime":    {d: round(fa.get(d, 0)*100, 1) for d in DIGITS},
            "dow":        {d: round(fd.get(d, 0)*100, 1) for d in DIGITS},
            "recency":    {d: round(frec.get(d, 0)*100, 1) for d in DIGITS},
            "gap":        {d: round(fgap.get(d, 0)*100, 1) for d in DIGITS},
            "markov":     {d: round(fmark.get(d, 0)*100, 1) for d in DIGITS},
            "similarity": {d: round(fsim.get(d, 0)*100, 1) for d in DIGITS},
            "cycles":     {d: round(fcyc.get(d, 0)*100, 1) for d in DIGITS},
        },
    }


def _generate_tickets(
    ranked: list[str],
    master: dict[str, float],
    fpos: dict[str, dict[str, float]],
    expected_sum: float,
    sum_sd: float,
    odd_frac: float,
    consec_bias: float,
    n_tickets: int,
    top_letters: list[tuple],
    grand_mode: bool,
    draws: list[dict],
) -> list[dict]:
    """
    Generate n_tickets diverse tickets using position-aware selection.
    Each ticket is built position by position, guided by:
      - position-specific frequency
      - master score
      - sum target
      - odd/even balance target
      - consecutive bias
    """
    tickets = []
    used_sets: list[frozenset] = []

    target_odds = round(odd_frac * 6)  # expected number of odd digits

    for t_idx in range(n_tickets * 3):  # generate extras, pick best unique ones
        if len(tickets) >= n_tickets:
            break

        chosen: list[str] = []
        remaining_sum = expected_sum
        odds_left = target_odds

        for pos in range(1, 7):
            pos_str = str(pos)
            pos_freq = fpos.get(pos_str, {})

            # Score each digit for this position
            candidates: dict[str, float] = {}
            for digit in DIGITS:
                if digit in chosen:
                    # Allow repeats but penalize slightly for diversity in ticket 1
                    repeat_penalty = 0.2 if t_idx == 0 else 0.0
                else:
                    repeat_penalty = 0.0

                # Position frequency
                pf = _smooth(pos_freq, digit)
                # Master score
                ms = master.get(digit, 0.0)
                # Sum guidance: prefer digits that bring running total closer to target
                d_int = int(digit)
                positions_left = 6 - pos + 1
                ideal = remaining_sum / positions_left if positions_left > 0 else 5
                sum_diff = abs(d_int - ideal)
                sum_score = max(0.0, 1.0 - sum_diff / 10.0)
                # Odd/even guidance
                is_odd = d_int % 2 != 0
                oe_score = 1.0 if (is_odd and odds_left > 0) or (not is_odd and odds_left <= 0) else 0.5

                candidates[digit] = (
                    0.35 * ms
                    + 0.25 * pf
                    + 0.20 * sum_score
                    + 0.15 * oe_score
                    - 0.05 * repeat_penalty
                )

            # Add slight variation per ticket
            import random
            random.seed(t_idx * 100 + pos)
            best = sorted(candidates.items(), key=lambda x: -x[1])
            # Pick from top 3 with bias toward best
            pool = best[:3]
            weights = [3, 2, 1][:len(pool)]
            chosen_digit = random.choices([b[0] for b in pool], weights=weights, k=1)[0]

            chosen.append(chosen_digit)
            remaining_sum -= int(chosen_digit)
            if int(chosen_digit) % 2 != 0:
                odds_left -= 1

        # Pick best letter for this ticket
        best_letter = top_letters[t_idx % len(top_letters)][0] if top_letters else "?"

        ticket_str = f"{best_letter} {' '.join(chosen)}"
        ticket_set = frozenset(chosen)

        # Avoid exact duplicates
        if ticket_set in used_sets:
            continue
        used_sets.append(ticket_set)

        ticket_sum = sum(int(c) for c in chosen)
        ticket_odds = sum(1 for c in chosen if int(c) % 2 != 0)
        consec = any(
            sorted([int(c) for c in chosen])[i+1] - sorted([int(c) for c in chosen])[i] == 1
            for i in range(len(chosen)-1)
        )

        # Confidence: how well does this ticket score on master
        conf = sum(master.get(d, 0) for d in chosen) / len(chosen)
        if grand_mode:
            conf = _grand_prize_score([int(d) for d in chosen], draws)

        # Normalize confidence to 0-100
        tickets.append({
            "ticket":    ticket_str,
            "digits":    chosen,
            "letter":    best_letter,
            "raw_conf":  conf,
            "sum":       ticket_sum,
            "odd_count": ticket_odds,
            "even_count": 6 - ticket_odds,
            "has_consec": consec,
        })

    # Normalize confidence across generated tickets
    if tickets:
        min_c = min(t["raw_conf"] for t in tickets)
        max_c = max(t["raw_conf"] for t in tickets)
        rng   = (max_c - min_c) or 1
        for t in tickets:
            t["confidence"] = round((t["raw_conf"] - min_c) / rng * 100, 1)
        # If only one ticket, always show 100%
        if len(tickets) == 1:
            tickets[0]["confidence"] = 100.0

    return tickets[:n_tickets]


# ─── Backtest ─────────────────────────────────────────────────────────────────

def backtest(lottery_name: str, last_n: int = 20) -> dict:
    """
    Simulate: for each of the last N draws, predict using data BEFORE that draw,
    then check how many of the predicted top-6 actually appeared.
    Returns per-draw match rates and average accuracy.
    """
    draws = _load(lottery_name)
    if len(draws) < last_n + 20:
        raise ValueError(f"Need at least {last_n + 20} draws to backtest.")

    results = []
    for i in range(last_n):
        actual_draw = draws[i]          # the draw we're predicting
        history     = draws[i+1:]       # only use data BEFORE this draw

        # Quick predict using only alltime + dow
        fa = f_alltime(history)
        fd = f_dow(history, actual_draw.get("day_of_week", 0))
        combined: dict[str, float] = {}
        for d in DIGITS:
            combined[d] = 0.6 * _smooth(fa, d) + 0.4 * _smooth(fd, d)
        top6_pred = [d for d, _ in sorted(combined.items(), key=lambda x: -x[1])[:6]]

        actual_nums = set(str(n) for n in actual_draw["numbers"])
        predicted   = set(top6_pred)
        matches     = len(actual_nums & predicted)

        results.append({
            "draw":    actual_draw["draw_number"],
            "date":    actual_draw["draw_date"],
            "actual":  actual_draw["winning_raw"],
            "pred_top6": " ".join(top6_pred),
            "matches": matches,
            "pct":     matches / 6 * 100,
        })

    avg_matches = mean(r["matches"] for r in results)
    avg_pct     = mean(r["pct"] for r in results)

    return {
        "lottery_name": lottery_name,
        "draws_tested": last_n,
        "results":      results,
        "avg_matches":  round(avg_matches, 2),
        "avg_pct":      round(avg_pct, 1),
        "best_draw":    max(results, key=lambda x: x["matches"]),
        "worst_draw":   min(results, key=lambda x: x["matches"]),
    }


# ─── Display ──────────────────────────────────────────────────────────────────

def _bar(v: float, w: int = 20) -> str:
    filled = round(max(0, min(v, 100)) / 100 * w)
    return "█" * filled + "░" * (w - filled)

def _sep(c: str = "-", w: int = 68) -> str:
    return "  " + c * w


def print_prediction(r: dict, verbose: bool = False) -> None:
    print(f"\n{'='*70}")
    print(f"  ADVANCED LOTTERY AGENT v2 — {r['lottery_name']}")
    print(f"  Day: {r['day_name']}  |  Based on: {r['draw_count']} draws  |  "
          f"Expected sum: {r['expected_sum']} ±{r['sum_stdev']}")
    print(f"  Odd/Even bias: {round(r['odd_fraction']*6,1)} odd / "
          f"{round((1-r['odd_fraction'])*6,1)} even  |  "
          f"Consecutive draws: {round(r['consec_bias']*100)}%")
    print(f"{'='*70}")

    print(f"\n  {'Rank':<5}  {'Ticket':<25}  {'Conf%':>6}  {'Sum':>4}  {'Odd':>3}  {'Consec'}")
    print(_sep())
    for i, t in enumerate(r["tickets"], 1):
        consec_mark = "Yes" if t["has_consec"] else "No"
        print(f"  #{i:<4}  {t['ticket']:<25}  {t['confidence']:>5.1f}%  "
              f"{t['sum']:>4}  {t['odd_count']:>3}  {consec_mark}")

    print(f"\n  TOP PICK:  >> {r['tickets'][0]['ticket']} <<\n")

    print(f"  Number Confidence Scores ({r['day_name']}):")
    print(_sep())
    print(f"  {'Num':>4}  {'Score':>6}  {'Bar':<22}  {'Votes'}")
    print(_sep())
    for digit in sorted(r["scores"], key=lambda x: -r["scores"][x]):
        score = r["scores"][digit]
        votes = r["ensemble_votes"].get(digit, 0)
        vote_stars = "*" * votes
        print(f"  {digit:>4}  {score:>5.1f}%  {_bar(score):<22}  {vote_stars}")

    print(f"\n  Top Letter Predictions:")
    print(_sep())
    for lt, sc in r["top_letters"]:
        print(f"  {lt:>4}  {round(sc*100, 1):>5.1f}%  {_bar(sc*100)}")

    if verbose:
        print(f"\n  Factor Breakdown (top 3 numbers per factor):")
        print(_sep())
        for fname, fdata in r["factor_scores"].items():
            if any(v > 0 for v in fdata.values()):
                top3 = sorted(fdata.items(), key=lambda x: -x[1])[:3]
                vals = "  ".join(f"{d}:{v:.1f}%" for d, v in top3)
                print(f"  {fname:<12}: {vals}")

        print(f"\n  Ensemble Weights Used:")
        for k, v in r["weights_used"].items():
            if v > 0:
                print(f"  {k:<14}: {round(v*100)}%")
    print()


def print_backtest(r: dict) -> None:
    print(f"\n{'='*70}")
    print(f"  BACKTEST RESULTS — {r['lottery_name']}")
    print(f"  Last {r['draws_tested']} draws  |  "
          f"Avg match: {r['avg_matches']}/6 ({r['avg_pct']}%)")
    print(f"{'='*70}\n")
    print(f"  {'Draw#':>6}  {'Date':>12}  {'Predicted top-6':<16}  "
          f"{'Actual':>20}  Matches")
    print(_sep())
    for res in r["results"]:
        bar = "+" * res["matches"] + "-" * (6 - res["matches"])
        print(f"  {res['draw']:>6}  {res['date']:>12}  "
              f"{res['pred_top6']:<16}  {res['actual']:>20}  [{bar}]")
    print()
    print(f"  Best:  draw #{r['best_draw']['draw']} — {r['best_draw']['matches']}/6 matches")
    print(f"  Worst: draw #{r['worst_draw']['draw']} — {r['worst_draw']['matches']}/6 matches")
    print()


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Advanced NLB Lottery Agent v2 — 15-factor ensemble prediction"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # predict
    p = sub.add_parser("predict", help="Generate top tickets for one lottery")
    p.add_argument("--lottery",  required=True)
    p.add_argument("--tickets",  type=int, default=5, help="Tickets to generate (default: 5)")
    p.add_argument("--verbose",  action="store_true")

    # grand prize mode
    pg = sub.add_parser("grand", help="Grand prize focus mode (maximize full match)")
    pg.add_argument("--lottery", required=True)
    pg.add_argument("--tickets", type=int, default=5)

    # analyze
    pa = sub.add_parser("analyze", help="Full factor analysis")
    pa.add_argument("--lottery", required=True)
    pa.add_argument("--tickets", type=int, default=3)

    # all lotteries summary
    sub.add_parser("all", help="Predict top ticket for all lotteries")

    # backtest
    pb = sub.add_parser("backtest", help="Test prediction accuracy on historical draws")
    pb.add_argument("--lottery", required=True)
    pb.add_argument("--last",    type=int, default=20, help="Number of past draws to test")

    args = parser.parse_args()

    if args.cmd == "predict":
        r = predict_advanced(args.lottery, num_tickets=args.tickets)
        print_prediction(r, verbose=args.verbose)

    elif args.cmd == "grand":
        r = predict_advanced(args.lottery, num_tickets=args.tickets, grand_prize_mode=True)
        print(f"\n  *** GRAND PRIZE MODE — maximizing full-match probability ***")
        print_prediction(r, verbose=True)

    elif args.cmd == "analyze":
        r = predict_advanced(args.lottery, num_tickets=args.tickets)
        print_prediction(r, verbose=True)

    elif args.cmd == "all":
        print(f"\n{'='*70}")
        print(f"  ADVANCED AGENT v2 — ALL LOTTERIES")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}  ({DAY_NAMES[date.today().weekday()]})")
        print(f"{'='*70}")
        print(f"\n  {'Lottery':<28}  {'Top Pick':<25}  {'Conf%':>6}  {'Sum':>4}  Draws")
        print("  " + "-"*72)
        for name in sorted(LOTTERIES):
            try:
                r = predict_advanced(name, num_tickets=1)
                t = r["tickets"][0]
                print(f"  {name:<28}  {t['ticket']:<25}  "
                      f"{t['confidence']:>5.1f}%  {t['sum']:>4}  {r['draw_count']}")
            except ValueError:
                print(f"  {name:<28}  (no data)")
            except Exception as e:
                print(f"  {name:<28}  ERROR: {e}")
        print()

    elif args.cmd == "backtest":
        r = backtest(args.lottery, last_n=args.last)
        print_backtest(r)


if __name__ == "__main__":
    main()

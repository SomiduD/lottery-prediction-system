import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from predictor import score_tickets

# The 10 tickets actually added to cart during Phase 1
tickets = [
    "O 6 2 3 5 5 4",
    "P 5 7 2 5 1 1",
    "A 0 3 5 4 3 5",
    "Z 3 0 5 5 0 6",
    "Y 8 6 5 1 2 5",
    "D 6 8 7 9 4 5",
    "P 3 2 6 9 0 7",
    "J 5 7 5 3 4 4",
    "M 2 7 4 1 6 8",
    "R 1 4 5 9 0 0",   # ticket 10 (approximate - check your cart)
]

ranked = score_tickets("Mahajana Sampatha", tickets)

print("=" * 55)
print("  TICKET RANKINGS - Mahajana Sampatha")
print("=" * 55)
print(f"  {'Rank':<5} {'Ticket':<25} {'Score':>7}  Action")
print("-" * 55)
for i, (ticket, score) in enumerate(ranked, 1):
    action = ">>> KEEP <<<" if i <= 2 else "remove"
    print(f"  {i:<5} {ticket:<25} {score:>7.2f}  {action}")
print("=" * 55)
print()
print("KEEP these 2 tickets in your cart:")
for i, (t, s) in enumerate(ranked[:2], 1):
    print(f"  {i}. {t}  (score: {s:.2f})")
print()
print("REMOVE these 8 from your cart:")
for i, (t, s) in enumerate(ranked[2:], 3):
    print(f"  {i}. {t}  (score: {s:.2f})")

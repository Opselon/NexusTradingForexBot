#!/usr/bin/env python
"""Forensic stats: exit-behavior patterns across all 146 closed ledger rows."""

import sqlite3
from collections import Counter, defaultdict

DB = r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db"
con = sqlite3.connect(DB)
cur = con.cursor()

rows = cur.execute(
    """
    SELECT ticket, direction, entry_price, close_price, status, exit_mechanism,
           net_pnl_usd, gross_pnl_usd, duration_seconds, MAE_usd, MFE_usd,
           initial_sl_price, final_sl_price, was_sl_modified, is_risk_free_hit,
           entry_reason, ai_confidence_at_open, market_regime_at_open, open_time,
           close_time, volume
    FROM audit_ledger
    """
).fetchall()

print("total ledger rows:", len(rows))
# dedupe by ticket (all are distinct tickets; 146 tickets / 32 orders)
print("tickets:", len({r[0] for r in rows}), "order_ids:", len({r[15] for r in rows}))

mech = Counter(r[5] for r in rows)
print("\nmechanisms:", dict(mech))

# Group by order (sibling legs share order_id)
by_order = defaultdict(list)
for r in rows:
    by_order[r[15]].append(r)
print("orders with >1 leg:", sum(1 for v in by_order.values() if len(v) > 1))

# Analyze per-ticket excursions
losers = [r for r in rows if r[5] in ("HARD_SL_HIT", "UNKNOWN") and r[9] is not None and r[9] < 0]
be_hits = [r for r in rows if r[5] == "BREAK_EVEN_SL_HIT"]
print("\n=== LOSERS (HARD_SL_HIT / UNKNOWN with negative MAE) ===")
print(
    "count:", len(losers), "median duration:", sorted(r[8] or 0 for r in losers)[len(losers) // 2]
)
print(
    "max loss excursion $:",
    min(r[9] for r in losers),
    "median:",
    sorted(r[9] for r in losers)[len(losers) // 2],
)
print(
    "max MFE $:",
    max(r[10] or 0 for r in losers),
    "median:",
    sorted(r[10] or 0 for r in losers)[len(losers) // 2],
)
print("max duration:", max(r[8] or 0 for r in losers))

print("\n=== BE HITS ===")
print(
    "count:",
    len(be_hits),
    "median duration:",
    sorted(r[8] or 0 for r in be_hits)[len(be_hits) // 2],
)
print(
    "with was_sl_modified:",
    sum(1 for r in be_hits if r[13]),
    "risk_free:",
    sum(1 for r in be_hits if r[14]),
)

# How many losers had meaningful MFE (could have exited at BE/+)?
print("\n=== losers with MFE > 0 (had profitable moments) ===")
profitable_moment = [r for r in losers if (r[10] or 0) > 0]
print("count:", len(profitable_moment), "of", len(losers))
mfe_positive = [r for r in profitable_moment if (r[10] or 0) > 5]
print("with MFE > $5:", len(mfe_positive))

# Giveback analysis: BE hit rows where final_sl moved beyond entry (risk-free) but closed at ~entry => they returned from profit to BE
print("\n=== giveback pattern: risk-free BE hits (SL moved into profit but exit ~at entry) ===")
rf = [r for r in be_hits if r[14] == 1]
print("count:", len(rf))

# Long holds > 400s
long_holds = [r for r in rows if (r[8] or 0) > 400]
print("\n=== holds > 400s ===")
for r in long_holds:
    print(f"  {r[0]} {r[1]} dur={r[8]} mech={r[5]} mae=${r[9]} mfe=${r[10]}")

# Duration buckets
print("\n=== duration buckets ===")
for lo, hi in [(0, 60), (60, 180), (180, 400), (400, 900), (900, 99999)]:
    n = sum(1 for r in rows if lo <= (r[8] or 0) < hi)
    print(f"  {lo:>4}-{hi:<6}s: {n}")

# Dominant entry reason
print("\nentry reasons:", dict(Counter(r[15] for r in rows)))
print("regimes at open:", dict(Counter(r[17] for r in rows)))
conf = [r[16] for r in rows if r[16]]
print(
    "confidence at open: min", min(conf), "max", max(conf), "median", sorted(conf)[len(conf) // 2]
)

# per-order aggregate PnL (all-zero due to legacy) but risk summary
print("\n=== avg initial SL distance (points) ===")
dist = [abs(float(r[4]) - float(r[11])) for r in rows if r[11] and r[4] and r[5] != ""]
print("median SL distance:", sorted(dist)[len(dist) // 2], "min:", min(dist), "max:", max(dist))

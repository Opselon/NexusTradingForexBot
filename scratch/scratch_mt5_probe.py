"""
BUG-046 REAL MT5 READ-ONLY PROBE
=================================
Proves the new lifecycle-based lookup finds the real broker deals for the
corrupted production outcomes. READ-ONLY: no orders, no writes.

Compares:
  old lookup (host-now - 1h)  -> expected 0 deals for the affected tickets
  new lookup (lifecycle-window, >= 24h) -> real deals with real PnL
"""

import sqlite3
import sys
from datetime import UTC, datetime, timedelta

import MetaTrader5 as mt5

OK = mt5.initialize()
if not OK:
    print("FATAL: MT5 init failed:", mt5.last_error())
    sys.exit(1)

# 1) Clock evidence
host_now = datetime.now(UTC)
deals_all = mt5.history_deals_get(host_now - timedelta(hours=48), host_now) or []
tick = mt5.symbol_info_tick("XAUUSD")
print("=== CLOCK EVIDENCE ===")
print(f"host now (UTC):        {host_now.isoformat()}")
if tick:
    tick_ts = datetime.fromtimestamp(tick.time, tz=UTC)
    print(f"broker tick (UTC):     {tick_ts.isoformat()}")
    print(f"broker-host skew:      {(host_now - tick_ts).total_seconds() / 3600:.2f} h")
print(f"deals in 48h:           {len(deals_all)}")

# 2) Affected outcome tickets from the production DB
con = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()
rows = cur.execute(
    "SELECT idempotency_key, execution_id, realized_r_multiple, realized_pnl_usd, "
    "outcome_timestamp FROM audit_experience_outcomes WHERE execution_id != '' "
    "ORDER BY outcome_timestamp"
).fetchall()
con.close()

print("\n=== AFFECTED OUTCOMES (zero-R) ===")
zero = [r for r in rows if abs(r["realized_r_multiple"]) < 1e-12]
print(f"outcomes: {len(rows)}, zero-R: {len(zero)}")

# 3) OLD lookup simulation: host-now-1h window
old_from = host_now - timedelta(hours=1)
old_deals = [d for d in deals_all if d.time >= int(old_from.timestamp())]
old_matches = 0
for r in zero:
    if any(d.position_id == int(r["execution_id"]) for d in old_deals):
        old_matches += 1
print(
    f"\n=== OLD LOOKUP (host-1h) === deals_in_window={len(old_deals)} matched_tickets={old_matches}"
)

# 4) NEW lookup simulation: full 48h window (>= lifecycle)
new_matches = 0
print("\n=== NEW LOOKUP (48h window) ===")
for r in zero[:15]:
    ticket = int(r["execution_id"])
    td = [d for d in deals_all if d.position_id == ticket]
    if td:
        profit = sum(d.profit for d in td if d.entry == 1)
        new_matches += 1
        print(
            f"  ticket={ticket} key={r['idempotency_key'][:20]} "
            f"deals={len(td)} closed_profit={profit:+.2f} "
            f"outcome_ts={r['outcome_timestamp'][11:19]}"
        )
    else:
        print(f"  ticket={ticket} key={r['idempotency_key'][:20]} NO DEALS IN 48h")
print(f"\nnew-lookup matched: {new_matches}/{len(zero)}")

mt5.shutdown()

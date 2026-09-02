import sqlite3
from collections import Counter

con = sqlite3.connect(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db")
con.row_factory = sqlite3.Row

# For the 237 mismatches, what is the ledger-side pattern?
rows = [dict(r) for r in con.execute("""
    SELECT b.trade_id, b.net_pnl AS bp, l.net_pnl_usd AS lp, l.exit_mechanism AS mech,
           l.exit_reason_source AS src, l.order_id
    FROM audit_broker_trades b JOIN audit_ledger l ON l.ticket = b.trade_id
    WHERE b.exit_time != '' AND abs(b.net_pnl - l.net_pnl_usd) > 0.01
""")]
print("mismatch rows:", len(rows))
print("by ledger exit_mechanism:", Counter(r["mech"] for r in rows))
print("by ledger exit_reason_source:", Counter(r["src"] for r in rows))
zero_lp = [r for r in rows if abs(r["lp"]) < 0.01]
print("ledger shows ZERO PnL but broker real:", len(zero_lp))
same_direction = [r for r in rows if (r["bp"] > 0) == (r["lp"] > 0) and abs(r["lp"]) >= 0.01]
print("same direction but different magnitude:", len(same_direction))
# split-family sample: same order_id, multiple tickets — ledger per-ticket vs broker per-deal?
multi = Counter(r["order_id"] for r in rows if r["order_id"])
print("multiplexed order_ids in mismatches:", len([k for k, v in multi.items() if v > 1]))
# First: how many ledger tickets exist per order_id globally
seen = con.execute("""
    SELECT order_id, COUNT(*) AS n FROM audit_ledger WHERE order_id != '' GROUP BY order_id HAVING n > 1
""").fetchall()
print("ledger order_ids with multiple tickets:", len(seen))
for r in con.execute("""SELECT ticket, net_pnl_usd, order_id, exit_time FROM audit_ledger WHERE order_id IN (
        SELECT order_id FROM audit_ledger WHERE order_id != '' GROUP BY order_id HAVING COUNT(*) > 1 LIMIT 3) ORDER BY order_id, ticket"""):
    print(dict(r))
con.close()
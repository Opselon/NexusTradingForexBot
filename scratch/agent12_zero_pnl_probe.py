import sqlite3
from collections import Counter

con = sqlite3.connect(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db")
con.row_factory = sqlite3.Row

# The 151 ledger-zero rows: are they the OLD seed/sim rows (ticket < 1e9)?
rows = [dict(r) for r in con.execute("""
    SELECT l.ticket, l.net_pnl_usd, l.exit_mechanism, l.symbol, b.net_pnl AS bp, b.source
    FROM audit_ledger l LEFT JOIN audit_broker_trades b ON b.trade_id = l.ticket
    WHERE l.net_pnl_usd = 0
""")]
old = [r for r in rows if r["ticket"] < 1_000_000_000]
new = [r for r in rows if r["ticket"] >= 1_000_000_000]
print("ledger-zero rows:", len(rows), "| small (sim/seed) tickets:", len(old), "| real broker tickets:", len(new))
print("small ticket zero rows sample:", old[:5])
real = [r for r in new if r["bp"] is not None and abs(float(r["bp"])) > 0.01]
print("real-broker tickets with zero ledger but broker PnL != 0:", len(real))
print("sample:", real[:5])

# exit_mechanism '' rows overall
mech = Counter(r["exit_mechanism"] or "''" for r in rows)
print("zero-row exit mechanisms:", mech.most_common(10))

# Are real-broker zero-ledger rows correlated with outcomes (experience)?
real_ids = [str(r["ticket"]) for r in real]
if real_ids:
    q = ",".join("?" * len(real_ids))
    outc = con.execute(f"SELECT execution_id, realized_pnl_usd FROM audit_experience_outcomes WHERE execution_id IN ({q})", real_ids).fetchall()
    print("outcomes for real-broker zero-ledger tickets:", len(outc), "sample:", [dict(x) for x in outc[:4]])
con.close()
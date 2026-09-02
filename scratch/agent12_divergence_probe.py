import sqlite3
from collections import Counter

con = sqlite3.connect(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db")
con.row_factory = sqlite3.Row
rows = [dict(r) for r in con.execute(
    "SELECT trade_id, net_pnl, master_order_id, source, exit_time FROM audit_broker_trades "
    "WHERE exit_time != '' ORDER BY exit_time DESC LIMIT 10000"
)]
led = {str(r["ticket"]): dict(r) for r in con.execute("SELECT ticket, net_pnl_usd FROM audit_ledger")}
mism, unmapped = [], 0
for b in rows:
    lr = led.get(str(b["trade_id"])) or led.get(str(b.get("master_order_id")))
    if lr is None:
        unmapped += 1
        continue
    if abs(float(b["net_pnl"] or 0) - float(lr["net_pnl_usd"] or 0)) > 0.01:
        mism.append({"t": b["trade_id"], "bp": round(float(b["net_pnl"] or 0), 2),
                     "lp": round(float(lr["net_pnl_usd"] or 0), 2), "src": b["source"]})
print("broker rows:", len(rows), "unmapped:", unmapped)
print("mismatches:", len(mism))
neg = [m for m in mism if (m["bp"] < 0 < m["lp"]) or (m["lp"] < 0 < m["bp"])]
print("sign-flipped:", len(neg))
zero_b = [m for m in mism if abs(m["bp"]) < 0.01]
zero_l = [m for m in mism if abs(m["lp"]) < 0.01]
print("broker-zero PnL:", len(zero_b), "ledger-zero PnL:", len(zero_l))
print("sample mismatches:", mism[:6])
print("mismatch sources:", Counter(m["src"] for m in mism))
# how many ledger tickets have NO broker row at all (ledger-only)?
led_only = [t for t in led if t not in {str(b["trade_id"]) for b in rows} and t not in {str(b.get("master_order_id")) for b in rows}]
print("ledger-only tickets (no broker row in sample):", len(led_only))
con.close()
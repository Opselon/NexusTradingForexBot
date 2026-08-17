#!/usr/bin/env python
"""Signal analysis for reversal/liquidity audit."""

import sqlite3

DB = r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db"
con = sqlite3.connect(DB)
cur = con.cursor()

rows = cur.execute(
    "SELECT reason_code, action, COUNT(*) FROM audit_signals GROUP BY reason_code, action ORDER BY 3 DESC LIMIT 18"
).fetchall()
print("reason_code / action / count")
for r in rows:
    print(r)

n = cur.execute(
    "SELECT COUNT(*) FROM audit_signals WHERE reason_code LIKE '%LIQUIDITY%' OR reason_code LIKE '%SWEEP%' OR reason_code LIKE '%REVERSAL%'"
).fetchone()[0]
print("liquidity/sweep/reversal signals:", n)
n2 = cur.execute("SELECT COUNT(*) FROM audit_signals WHERE action='CLOSE_POSITION'").fetchone()[0]
print("CLOSE_POSITION signals emitted by policy:", n2)

# AI_REVERSAL_SIGNAL specifically
n3 = cur.execute(
    "SELECT COUNT(*) FROM audit_signals WHERE reason_code='AI_REVERSAL_SIGNAL'"
).fetchone()[0]
print("AI_REVERSAL_SIGNAL proposals:", n3)

# any close dispatched in audit_orders
n4 = cur.execute(
    "SELECT COUNT(*) FROM audit_orders WHERE action LIKE '%close%' OR action LIKE '%CLOSE%'"
).fetchone()[0]
print("close-ish audit_orders rows:", n4)

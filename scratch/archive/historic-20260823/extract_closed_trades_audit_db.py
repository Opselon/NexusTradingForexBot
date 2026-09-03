#!/usr/bin/env python
"""Forensic extraction: real closed-trade data from artifacts/audit.db."""

import sqlite3

DB = r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db"
con = sqlite3.connect(DB)
cur = con.cursor()

print("=== audit_ledger: distinct tickets and mechanisms ===")
rows = cur.execute(
    "SELECT COUNT(*), COUNT(DISTINCT ticket), COUNT(DISTINCT order_id) FROM audit_ledger"
).fetchone()
print("total rows:", rows[0], "distinct tickets:", rows[1], "distinct order_id:", rows[2])

print()
print("=== exit mechanism distribution ===")
for r in cur.execute(
    "SELECT exit_mechanism, status, COUNT(*) FROM audit_ledger GROUP BY exit_mechanism, status ORDER BY 3 DESC"
):
    print(r)

print()
print("=== pnl distribution (non-zero) ===")
for r in cur.execute(
    "SELECT COUNT(*) FILTER (WHERE net_pnl_usd IS NOT NULL AND net_pnl_usd != 0), "
    "COUNT(*) FILTER (WHERE net_pnl_usd IS NULL), "
    "COUNT(*) FILTER (WHERE net_pnl_usd = 0), "
    "SUM(net_pnl_usd) FROM audit_ledger"
):
    print("net!=0:", r[0], "net IS NULL:", r[1], "net==0:", r[2], "sum:", r[3])

print()
print("=== per-ticket summary (dedup by ticket, newest first) ===")
rows = cur.execute(
    """
    SELECT ticket, direction, entry_price, close_price, status, exit_mechanism,
           net_pnl_usd, gross_pnl_usd, duration_seconds, MAE_usd, MFE_usd,
           initial_sl_price, final_sl_price, was_sl_modified, is_risk_free_hit,
           entry_reason, ai_confidence_at_open, market_regime_at_open,
           open_time, close_time
    FROM audit_ledger
    WHERE ticket IN (SELECT MAX(ticket) FROM audit_ledger GROUP BY order_id)
    ORDER BY open_time DESC
    """
).fetchall()
print(
    f"{'TICKET':>13} {'DIR':4} {'ENTRY':>9} {'CLOSE':>9} {'STATUS':10} {'MECH':22} "
    f"{'NET$':>8} {'GROSS$':>8} {'DUR':>6} {'MAE$':>7} {'MFE$':>7} {'SLM':>3} {'RF':>2} "
    f"{'CONF':>5} {'REGIME':14} OPEN_TIME"
)
for r in rows:
    print(
        f"{r[0]!s:>13} {r[1]!s:4} {r[2]!s:>9} {r[3]!s:>9} {r[4]!s:10} {r[5]!s:22} "
        f"{r[6]!s:>8} {r[7]!s:>8} {r[8]!s:>6} {r[9]!s:>7} {r[10]!s:>7} "
        f"{r[11]!s:>3} {r[12]!s:>2} {r[13]!s:>5} {r[14]!s:14} {str(r[18])[:19]}"
    )

import sqlite3

con = sqlite3.connect(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db")
con.row_factory = sqlite3.Row

# Characterize the 237 divergences: fee/commission/swap differences?
print("=== divergence anatomy ===")
for r in con.execute("""
    SELECT b.trade_id, b.gross_pnl, b.commission, b.swap, b.fee, b.net_pnl,
           l.net_pnl_usd, l.exit_mechanism, l.exit_reason_source
    FROM audit_broker_trades b JOIN audit_ledger l ON l.ticket = b.trade_id
    WHERE b.exit_time != '' AND abs(b.net_pnl - l.net_pnl_usd) > 0.01
      AND b.source IN ('BROKER_DEALS')
    LIMIT 8
"""):
    print(dict(r))

print()
print("=== broker net = gross + comm + swap + fee? ===")
for r in con.execute("""
    SELECT COUNT(*) AS n,
           SUM(CASE WHEN abs((gross_pnl + COALESCE(commission,0) + COALESCE(swap,0) + COALESCE(fee,0)) - net_pnl) > 0.001 THEN 1 ELSE 0 END) AS broken
    FROM audit_broker_trades WHERE source IN ('BROKER_DEALS')
"""):
    print(dict(r))

print()
print("=== ledger net vs broker net: is ledger missing swap/commission? ===")
# for the 13 sign-flipped
for r in con.execute("""
    SELECT b.trade_id, b.gross_pnl, b.commission, b.swap, b.fee, b.net_pnl, l.net_pnl_usd
    FROM audit_broker_trades b JOIN audit_ledger l ON l.ticket = b.trade_id
    WHERE b.exit_time != '' AND ((b.net_pnl < 0 AND l.net_pnl_usd > 0) OR (l.net_pnl_usd < 0 AND b.net_pnl > 0))
    LIMIT 6
"""):
    print(dict(r))

# does the ledger have commission/swap columns populated?
cols = [c[1] for c in con.execute("PRAGMA table_info(audit_ledger)")]
print()
print("ledger cols incl commission/swap:", "commission" in cols, "swap" in cols)
r = con.execute("SELECT COUNT(*), SUM(CASE WHEN commission != 0 THEN 1 ELSE 0 END), SUM(CASE WHEN swap != 0 THEN 1 ELSE 0 END) FROM audit_ledger").fetchone()
print("ledger rows / nonzero commission / nonzero swap:", tuple(r))
con.close()
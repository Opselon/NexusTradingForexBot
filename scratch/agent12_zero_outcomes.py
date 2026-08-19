import sqlite3

con = sqlite3.connect(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db")
con.row_factory = sqlite3.Row

# 1) The 32 zero-outcomes: are they reconstructed from broker deals?
rows = [dict(r) for r in con.execute("""
    SELECT o.execution_id, o.realized_pnl_usd, o.realized_r_multiple, o.is_executed, o.is_closed,
           o.outcome_timestamp, o.exit_reason
    FROM audit_experience_outcomes o
    WHERE o.realized_pnl_usd = 0
""")]
print("zero outcomes:", len(rows))
# same tickets in broker_trades?
tickets = [r["execution_id"] for r in rows]
found = 0
if tickets:
    q = ",".join("?" * len(tickets))
    found = con.execute(f"SELECT COUNT(*) FROM audit_broker_trades WHERE trade_id IN ({q})", tickets).fetchone()[0]
print("of these, tickets present in broker_trades:", found)

# 2) is there an experience record for each with request_id?
if tickets:
    exp = con.execute(f"SELECT execution_id, request_id, idempotency_key FROM audit_experiences WHERE execution_id IN ({q})", tickets).fetchall()
    no_req = [dict(x) for x in exp if not x["request_id"]]
    print("experiences for zero outcomes:", len(exp), "| missing request_id:", len(no_req))

# 3) When did the ledger last get a non-zero PnL row vs the zero rows?
r = con.execute("SELECT MIN(close_time), MAX(close_time) FROM audit_ledger WHERE net_pnl_usd != 0").fetchone()
print("non-zero ledger close_time range:", tuple(r))
r2 = con.execute("SELECT MIN(close_time), MAX(close_time) FROM audit_ledger WHERE net_pnl_usd = 0").fetchone()
print("zero ledger close_time range:", tuple(r2))
con.close()
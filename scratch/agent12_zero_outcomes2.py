import sqlite3

con = sqlite3.connect(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db")
con.row_factory = sqlite3.Row

# Are the 32 zero outcomes linked to experiences via other keys?
exp = [dict(r) for r in con.execute("""
    SELECT experience_id, request_id, execution_id, idempotency_key, action, strategy_id
    FROM audit_experiences LIMIT 5
""")]
print("experience sample:", exp)

# join outcomes to experiences on idempotency_key or execution_id
r = con.execute("""
    SELECT o.idempotency_key, o.execution_id, e.experience_id, e.request_id
    FROM audit_experience_outcomes o
    LEFT JOIN audit_experiences e ON e.idempotency_key = o.idempotency_key
    WHERE o.realized_pnl_usd = 0 LIMIT 5
""").fetchall()
print("join attempt:", [dict(x) for x in r])

# what does the 32-outcome batch look like? (one window? one recovery run?)
r = con.execute("SELECT MIN(outcome_timestamp), MAX(outcome_timestamp) FROM audit_experience_outcomes WHERE realized_pnl_usd = 0").fetchone()
print("zero outcomes window:", tuple(r))

# outcome payloads for a zero row
r = con.execute("SELECT payload FROM audit_experience_outcomes WHERE realized_pnl_usd = 0 LIMIT 1").fetchone()
print("payload:", str(r["payload"])[:600] if r else None)
con.close()
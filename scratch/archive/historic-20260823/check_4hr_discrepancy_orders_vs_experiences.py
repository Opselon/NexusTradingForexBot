"""CHECK THE 4-HOUR DISCREPANCY: audit_orders at 02:10-04:28 vs experiences at 05:10-07:21."""

import sqlite3

con = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

print("=== audit_orders time range ===")
r = cur.execute("SELECT MIN(timestamp), MAX(timestamp) FROM audit_orders").fetchone()
print("orders:", r[0], "..", r[1])

print("\n=== audit_signals time range ===")
r = cur.execute("SELECT MIN(generated_at), MAX(generated_at) FROM audit_signals").fetchone()
print("signals:", r[0], "..", r[1])

print("\n=== audit_experiences decision time range ===")
r = cur.execute(
    "SELECT MIN(decision_timestamp), MAX(decision_timestamp) FROM audit_experiences"
).fetchone()
print("experiences:", r[0], "..", r[1])

print("\n=== audit_experience_outcomes outcome time range ===")
r = cur.execute(
    "SELECT MIN(outcome_timestamp), MAX(outcome_timestamp) FROM audit_experience_outcomes"
).fetchone()
print("outcomes:", r[0], "..", r[1])

print("\n=== audit_ledger time range ===")
r = cur.execute("SELECT MIN(open_time), MAX(close_time) FROM audit_ledger").fetchone()
print("ledger:", r[0], "..", r[1])

print("\n=== audit_account_snapshots time range ===")
r = cur.execute("SELECT MIN(timestamp), MAX(timestamp) FROM audit_account_snapshots").fetchone()
print("snapshots:", r[0], "..", r[1])

# The first phantom protection row at 02:10 for ticket 152487837184 - what signal had that ticket?
print("\n=== first audit_orders rows per ticket (grouped) ===")
rows = cur.execute(
    "SELECT ticket, MIN(timestamp) AS first_ts, COUNT(*) n FROM audit_orders "
    "WHERE ticket > 1000000 GROUP BY ticket ORDER BY first_ts LIMIT 25"
).fetchall()
for r in rows:
    print(f"  ticket={r['ticket']} first_ts={r['first_ts']} rows={r['n']}")

con.close()

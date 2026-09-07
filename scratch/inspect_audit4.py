import sqlite3
conn = sqlite3.connect('file:artifacts/audit.db?mode=ro', uri=True)
c = conn.cursor()
print("=== registry rows ===")
for r in c.execute("SELECT model_id, model_version, lifecycle_status, registered_at FROM experience_model_registry ORDER BY id DESC LIMIT 12"):
    print(r)
print("=== date range ===")
print(c.execute("SELECT MIN(decision_timestamp), MAX(decision_timestamp) FROM audit_experiences").fetchone())
print("=== outcomes by date ===")
print(c.execute("SELECT MIN(outcome_timestamp), MAX(outcome_timestamp) FROM audit_experience_outcomes").fetchone())
print("=== real trades (executed) over time ===")
for r in c.execute("SELECT substr(e.decision_timestamp,1,10) d, COUNT(*) FROM audit_experiences e JOIN audit_experience_outcomes o ON o.idempotency_key=e.idempotency_key WHERE o.is_executed=1 GROUP BY 1 ORDER BY 1"):
    print(r)
conn.close()

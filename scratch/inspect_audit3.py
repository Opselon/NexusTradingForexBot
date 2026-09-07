import sqlite3, json
conn = sqlite3.connect('file:artifacts/audit.db?mode=ro', uri=True)
c = conn.cursor()
cols = [r[1] for r in c.execute("PRAGMA table_info(audit_experiences)")]
print("exp cols:", cols[:12], "...")
print("=== experience outcomes summary ===")
for r in c.execute("SELECT exit_reason, COUNT(*), ROUND(AVG(realized_r_multiple),3), ROUND(SUM(realized_r_multiple),2) FROM audit_experience_outcomes GROUP BY 1 ORDER BY 2 DESC LIMIT 14"):
    print(r)
print("=== executed+closed merged ===")
n = c.execute("""
SELECT COUNT(*) FROM audit_experiences e
JOIN audit_experience_outcomes o ON o.idempotency_key = e.idempotency_key
WHERE o.is_executed=1 AND o.is_closed=1 AND o.exit_reason NOT LIKE '%UNFILLED%'
""").fetchone()[0]
print("closed executed trades:", n)
print("=== registry ===")
for r in c.execute("SELECT model_id, model_version, lifecycle_status, status, registered_at FROM experience_model_registry ORDER BY registered_at DESC LIMIT 12"):
    print(r)
print("=== date range ===")
print(c.execute("SELECT MIN(decision_timestamp), MAX(decision_timestamp) FROM audit_experiences").fetchone())
conn.close()

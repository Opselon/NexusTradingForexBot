import sqlite3
conn = sqlite3.connect('file:artifacts/audit.db?mode=ro', uri=True)
c = conn.cursor()
print("=== training_runs ===")
try:
    for r in c.execute("SELECT run_id, status, created_at FROM training_runs ORDER BY id DESC LIMIT 10"):
        print(r)
except Exception as e:
    print("no training_runs table:", e)
print("=== governance state ===")
try:
    for r in c.execute("SELECT * FROM model_governance_state LIMIT 5"):
        print(r)
except Exception as e:
    print(e)
print("=== shadow runs ===")
try:
    for r in c.execute("SELECT COUNT(*) FROM shadow_runs"):
        print(r)
except Exception as e:
    print(e)
conn.close()

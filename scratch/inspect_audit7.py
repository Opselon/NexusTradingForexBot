import sqlite3
conn = sqlite3.connect('file:artifacts/audit.db?mode=ro', uri=True)
c = conn.cursor()
print("=== training_runs cols ===")
print([r[1] for r in c.execute("PRAGMA table_info(training_runs)")])
print("=== training_runs rows ===")
for r in c.execute("SELECT run_id, status, dataset_id, started_at FROM training_runs ORDER BY started_at DESC LIMIT 10"):
    print(r)
conn.close()

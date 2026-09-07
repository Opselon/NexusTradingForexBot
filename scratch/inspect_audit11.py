import sqlite3
conn = sqlite3.connect('file:artifacts/audit.db?mode=ro', uri=True)
c = conn.cursor()
print("=== research_runs status ===")
for r in c.execute("SELECT status, COUNT(*) FROM research_runs GROUP BY 1"):
    print(r)
print("=== research_gates gate results ===")
for r in c.execute("SELECT gate_name, status, COUNT(*) FROM research_gates GROUP BY 1,2 ORDER BY 1 LIMIT 20"):
    print(r)
conn.close()

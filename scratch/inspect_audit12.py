import sqlite3
conn = sqlite3.connect('file:artifacts/audit.db?mode=ro', uri=True)
c = conn.cursor()
print([r[1] for r in c.execute("PRAGMA table_info(research_gates)")])
for r in c.execute("SELECT gate, status, COUNT(*) FROM research_gates GROUP BY 1,2 ORDER BY 1 LIMIT 25"):
    print(r)
conn.close()

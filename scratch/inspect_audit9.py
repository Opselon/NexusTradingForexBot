import sqlite3
conn = sqlite3.connect('file:artifacts/audit.db?mode=ro', uri=True)
c = conn.cursor()
print([r[1] for r in c.execute("PRAGMA table_info(model_governance_events)")])
for r in c.execute("SELECT event_kind, COUNT(*) FROM model_governance_events GROUP BY 1 ORDER BY 2 DESC LIMIT 15"):
    print(r)
conn.close()

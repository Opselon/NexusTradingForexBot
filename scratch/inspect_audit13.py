import sqlite3
conn = sqlite3.connect('file:artifacts/audit.db?mode=ro', uri=True)
c = conn.cursor()
for r in c.execute("SELECT gate_type, status, COUNT(*) FROM research_gates GROUP BY 1,2 ORDER BY 1,3 DESC LIMIT 30"):
    print(r)
print("=== strategy_registry lifecycle ===")
for r in c.execute("SELECT lifecycle, COUNT(*) FROM strategy_registry GROUP BY 1"):
    print(r)
conn.close()

import sqlite3
conn = sqlite3.connect('file:artifacts/audit.db?mode=ro', uri=True)
c = conn.cursor()
for r in c.execute("SELECT event, stage, model_id, new_state, reason, timestamp FROM model_governance_events ORDER BY timestamp DESC LIMIT 15"):
    print(r)
conn.close()

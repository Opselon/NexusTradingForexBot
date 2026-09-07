import sqlite3
conn = sqlite3.connect('file:artifacts/audit.db?mode=ro', uri=True)
c = conn.cursor()
print("=== shadow tables ===")
for t in ['shadow_runs','shadow_decisions','shadow_comparisons','shadow_promotions','model_governance_events','model_governance_state','model_promotion_audit']:
    try:
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"{n:>6}  {t}")
    except Exception as e:
        print(f"  miss {t}: {e}")
print("=== governance events sample ===")
for r in c.execute("SELECT event_type, COUNT(*) FROM model_governance_events GROUP BY 1 ORDER BY 2 DESC LIMIT 12"):
    print(r)
conn.close()

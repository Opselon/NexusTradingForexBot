import sqlite3, json
conn = sqlite3.connect('file:artifacts/audit.db?mode=ro', uri=True)
c = conn.cursor()
# Check feature snapshot retention in payload
row = c.execute("SELECT payload FROM audit_experiences WHERE length(payload) > 1000 LIMIT 1").fetchone()
if row:
    p = json.loads(row[0])
    print("keys:", sorted(p.keys())[:30])
    fs = p.get('feature_snapshot') or {}
    vals = fs.get('values') or []
    print("feature dim:", fs.get('feature_dimension'), "values len:", len(vals), "schema:", fs.get('feature_schema_id'))
# closed trades with nonzero R
n = c.execute("SELECT COUNT(*) FROM audit_experience_outcomes WHERE is_executed=1 AND is_closed=1 AND realized_r_multiple != 0").fetchone()[0]
print("closed trades with nonzero R:", n)
# nonzero R distribution
for r in c.execute("SELECT CASE WHEN realized_r_multiple>0 THEN 'WIN' WHEN realized_r_multiple<0 THEN 'LOSS' ELSE 'ZERO' END, COUNT(*), ROUND(AVG(realized_r_multiple),3) FROM audit_experience_outcomes WHERE is_executed=1 AND is_closed=1 GROUP BY 1"):
    print(r)
conn.close()

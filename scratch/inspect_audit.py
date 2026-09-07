import sqlite3
conn = sqlite3.connect('file:artifacts/audit.db?mode=ro', uri=True)
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(len(tables), 'tables')
for t in sorted(tables):
    try:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        if n: print(f"{n:>9}  {t}")
    except Exception as e:
        print('ERR', t, e)
conn.close()

"""Check for older logs / other engine processes / where phantom tickets came from."""

import glob
import os

# 1) Any other log files anywhere?
print("=== log-like files ===")
for pat in [
    "artifacts/**/*.log*",
    "logs/**/*.log*",
    "*.log*",
]:
    for f in sorted(glob.glob(pat, recursive=True))[:30]:
        print(" ", f, os.path.getsize(f) if os.path.isfile(f) else "")

# 2) pidfile
for p in ["artifacts/engine.pid", "engine.pid", "artifacts/*.pid"]:
    for f in glob.glob(p):
        print("pidfile:", f, open(f).read().strip() if os.path.isfile(f) else "")

# 3) The experience payload decision ids -> do any experiences reference the
#    broker's REAL position ids (1524868..1524870..)?
import sqlite3

con = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
cur = con.cursor()
rows = cur.execute(
    "SELECT idempotency_key, execution_id FROM audit_experiences WHERE execution_id != ''"
).fetchall()
print("\n=== experiences WITH execution_id (non-empty) ===")
for r in rows:
    print(" ", r[0][:24], "->", r[1])
con.close()

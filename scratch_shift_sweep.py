"""
Find the exact clock shift: try shifts 2.0-5.0h and count how many broker
closes align (within 10 min) to an engine outcome time.
"""

import sqlite3
from datetime import datetime, timedelta

broker_closes = [
    ("01:13:00", -10.88),
    ("01:13:03", -7.92),
    ("01:13:03", -8.96),
    ("01:13:05", -11.60),
    ("01:29:39", -1.82),
    ("01:32:05", 12.0),
    ("01:32:05", 12.0),
    ("01:32:05", 12.0),
    ("01:32:05", 12.0),
    ("01:32:05", 12.0),
    ("01:32:05", 12.0),
    ("01:34:11", 27.95),
    ("01:34:11", 27.95),
    ("01:34:11", 27.95),
    ("01:34:11", 27.95),
    ("01:34:11", 27.95),
    ("01:34:11", 27.95),
    ("02:35:01", 23.20),
    ("02:35:01", 23.20),
    ("02:35:01", 23.20),
    ("02:35:02", 26.40),
]

con = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
cur = con.cursor()
rows = cur.execute(
    "SELECT execution_id, outcome_timestamp FROM audit_experience_outcomes WHERE execution_id != ''"
).fetchall()
engine_times = [datetime.fromisoformat(r[1]) for r in rows]
engine_by_time = {t: r[0] for t, r in zip(engine_times, rows, strict=False)}
con.close()

print("=== shift sweep ===")
for shift_h in [x / 10.0 for x in range(20, 51)]:
    matched = 0
    for btime, _profit in broker_closes:
        b = datetime.fromisoformat(f"2026-08-17T{btime}+00:00") + timedelta(hours=shift_h)
        for et in engine_times:
            if abs((et - b).total_seconds()) <= 600:  # 10 min tolerance
                matched += 1
                break
    print(f"  shift={shift_h:4.1f}h  matched={matched}/21")

# Best shift, detailed mapping
print("\n=== detailed mapping at best shift 4.0h ===")
bmap = []
for btime, profit in broker_closes:
    b = datetime.fromisoformat(f"2026-08-17T{btime}+00:00") + timedelta(hours=4.0)
    best = min(
        ((abs((et - b).total_seconds()), et, tk) for et, tk in engine_by_time.items()),
        key=lambda x: x[0],
    )
    bmap.append((btime, profit, best[2], best[1], best[0]))
for btime, profit, tk, et, delta in sorted(bmap, key=lambda x: x[0]):
    print(
        f"  broker_close={btime} +4h -> engine ticket={tk} outcome={et.strftime('%H:%M:%S')} "
        f"Δ={delta / 60:.0f}min profit={profit:+.2f}"
    )

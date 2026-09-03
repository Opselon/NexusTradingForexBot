"""
CLOCK-SHIFT VERIFICATION: map broker real deal times (+3h shift) against
engine-recorded ticket open times. If they align, the engine was running on
a clock ~3h behind and mislabeled REAL broker positions with WRONG TIMES.
"""

import sqlite3
from datetime import datetime, timedelta

# Broker real closes (from MT5 probes): position_id -> (close_time_utc, profit)
broker_closes = {
    152486859966: ("01:13:00", -10.88),
    152486859992: ("01:13:03", -7.92),
    152486860004: ("01:13:03", -8.96),
    152486860012: ("01:13:05", -11.60),
    152486906832: ("01:29:39", -1.82),
    152486910279: ("01:32:05", 12.00),
    152486910300: ("01:32:05", 12.00),
    152486910317: ("01:32:05", 12.00),
    152486910332: ("01:32:05", 12.00),
    152486910359: ("01:32:05", 12.00),
    152486910376: ("01:32:05", 12.00),
    152486919298: ("01:34:11", 27.95),
    152486919320: ("01:34:11", 27.95),
    152486919343: ("01:34:11", 27.95),
    152486919365: ("01:34:11", 27.95),
    152486919378: ("01:34:11", 27.95),
    152486919392: ("01:34:11", 27.95),
    152487091374: ("02:35:01", 23.20),
    152487091385: ("02:35:01", 23.20),
    152487091414: ("02:35:01", 23.20),
    152487091510: ("02:35:02", 26.40),
}

# Engine outcomes: ticket -> outcome_timestamp
con = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
cur = con.cursor()
rows = cur.execute(
    "SELECT execution_id, outcome_timestamp FROM audit_experience_outcomes WHERE execution_id != ''"
).fetchall()
engine = {r[0]: r[1] for r in rows}
con.close()

print("=== broker real closes (UTC) vs engine outcomes (UTC) — shift test ===")
print("Broker position ids are NOT the same numbers as engine tickets, so compare TIMES:")
shift_hours = 3.0
for pos_id, (btime, profit) in sorted(broker_closes.items()):
    # broker close in UTC + 3h = engine-clock equivalent
    b = datetime.fromisoformat(f"2026-08-17T{btime}+00:00") + timedelta(hours=shift_hours)
    # find engine-outcome tickets whose outcome_timestamp is nearest to b
    near = sorted(
        ((abs(datetime.fromisoformat(ts) - b), tk) for tk, ts in engine.items()),
        key=lambda x: x[0],
    )[:1]
    if near and near[0][0] < timedelta(minutes=30):
        tk, delta = near[0][1], near[0][0]
        print(
            f"  broker pos {pos_id} close {btime} +3h={b.strftime('%H:%M:%S')} "
            f"profit={profit:+.2f}  -> engine ticket {tk} outcome {engine[tk][11:19]} "
            f"Δ={delta.total_seconds() / 60:.0f}min"
        )
    else:
        print(
            f"  broker pos {pos_id} close {btime} +3h={b.strftime('%H:%M:%S')} profit={profit:+.2f}  -> no engine outcome near"
        )

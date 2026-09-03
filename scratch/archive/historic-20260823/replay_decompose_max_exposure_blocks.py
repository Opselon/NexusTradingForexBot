"""Historical replay: decompose MAX_EXPOSURE_REACHED blocks using broker truth."""

import sqlite3
from datetime import UTC, datetime

con = sqlite3.connect("file:artifacts/audit.db?mode=ro", uri=True)
cur = con.cursor()

WINDOW = "2026-08-17T02:49:00"

rows = cur.execute(
    """
    SELECT generated_at, reason_code, payload FROM audit_signals
    WHERE reason_code LIKE 'MAX_EXPOSURE%' AND generated_at >= ?
    ORDER BY generated_at
    """,
    (WINDOW,),
).fetchall()

# Build broker-truth timeline: for each minute, did broker have an active
# pending or position? Use audit_broker_orders rows (state 0/1/7/8/9 = active).
# Broker epochs are server-local (+3h). Real UTC = epoch - 10800.
active_by_minute: dict[int, int] = {}
orders = cur.execute(
    """
    SELECT time_setup, time_done, state FROM audit_broker_orders
    WHERE comment IN ('NSE_PENDING','NSE_CLOSE','NSE_MARKET','NSE_DYNAMIC_SIZED')
    """
).fetchall()
for setup, done, _state in orders:
    if not setup:
        continue
    s = int(setup) - 10800
    d = int(done) - 10800 if done else s
    for t in range(s // 60, max(d // 60, s // 60) + 1):
        active_by_minute[t] = active_by_minute.get(t, 0) + 1

print(f"signals in window: {len(rows)}")
n_legit = 0
n_stale = 0
n_unknown = 0
for ts, _reason, _payload in rows:
    try:
        t = datetime.fromisoformat(ts)
        minute = int(t.timestamp()) // 60
    except Exception:
        n_unknown += 1
        continue
    # Actual broker exposure at that minute
    broker_active = active_by_minute.get(minute, 0) > 0
    if broker_active:
        n_legit += 1
    elif minute >= 1787029800:  # after last broker-orders sync (02:26Z real 05:26 broker)
        # The broker history table stops at 02:26Z; signals after that cannot be
        # cross-checked from the DB — classify UNKNOWN (read-only range limit).
        n_unknown += 1
    else:
        n_stale += 1

print(f"LEGITIMATE (broker exposure real): {n_legit}")
print(f"STALE (broker had nothing):       {n_stale}")
print(f"UNKNOWN (outside DB sync range):  {n_unknown}")

# After-02:26Z signals (the crash window): the log shows the engine froze.
after = sum(
    1 for ts, _, _ in rows if datetime.fromisoformat(ts) >= datetime(2026, 8, 18, 2, 30, tzinfo=UTC)
)
print(f"signals after 02:30Z (crash window): {after}")

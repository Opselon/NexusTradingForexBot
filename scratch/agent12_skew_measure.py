import sqlite3
import statistics
from collections import Counter
from datetime import UTC, datetime

con = sqlite3.connect(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db")
con.row_factory = sqlite3.Row

# Proper clock skew: broker server-local epoch vs host UTC. Broker GMT+3 means
# local time == UTC+3, so a UTC-naive parse would show ~+3h. But the stored
# rows carry +00:00 — are they already normalized or raw server-local?
rows = [
    dict(r)
    for r in con.execute("""
    SELECT entry_time, exit_time FROM audit_broker_trades
    WHERE entry_time != '' ORDER BY exit_time DESC LIMIT 1000
""")
]
naive_offsets = []
aware_offsets = []
for r in rows:
    for col in ("entry_time", "exit_time"):
        raw = str(r.get(col) or "")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                aware_offsets.append((dt - datetime.now(UTC)).total_seconds())
        except ValueError:
            continue
if aware_offsets:
    print("aware samples:", len(aware_offsets))
    print("median offset vs host UTC (s):", round(statistics.median(aware_offsets), 1))
    print("min/max:", round(min(aware_offsets), 1), round(max(aware_offsets), 1))
    # distribution of offsets in hours
    buckets = Counter(round(o / 3600.0, 1) for o in aware_offsets)
    print("offset buckets (hours):", dict(sorted(buckets.items(), key=lambda x: -x[1])[:8]))
con.close()

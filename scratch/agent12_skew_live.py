import sqlite3
from datetime import UTC, datetime, timedelta
from statistics import median

con = sqlite3.connect(r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\artifacts\audit.db")
con.row_factory = sqlite3.Row

now = datetime.now(UTC)
# Broker rows older than 6h are HISTORICAL RECONSTRUCTIONS (synced_at shows
# they were backfilled from broker history in one batch). Only rows whose
# synced_at is within 6h of now represent the ACTIVE session -> live clock.
rows = [dict(r) for r in con.execute("""
    SELECT entry_time, exit_time, synced_at FROM audit_broker_trades
    WHERE exit_time != '' AND synced_at >= ?
    ORDER BY synced_at DESC LIMIT 2000
""", ((now - timedelta(hours=6)).isoformat(),))]
offs = []
for r in rows:
    for col in ("entry_time", "exit_time"):
        try:
            dt = datetime.fromisoformat(str(r.get(col) or "").replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                offs.append((dt - now).total_seconds())
        except ValueError:
            continue
print("rows synced in last 6h:", len(rows), "| offset samples:", len(offs))
if offs:
    print("median offset vs host UTC (s):", round(median(offs), 1))
    from collections import Counter
    print("hour buckets:", dict(sorted(Counter(round(o / 3600.0, 1) for o in offs).items(), key=lambda x: -x[1])[:6]))
con.close()
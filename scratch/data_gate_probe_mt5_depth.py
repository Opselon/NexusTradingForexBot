"""Quick MT5 history-depth probe (read-only)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import MetaTrader5 as mt5

if not mt5.initialize():
    print("FATAL init:", mt5.last_error())
    sys.exit(1)
print("version:", mt5.version())

SYM = "XAUUSD"
TFS = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

for name, tf in TFS.items():
    # 1) most recent bars
    recent = mt5.copy_rates_from_pos(SYM, tf, 0, 3)
    # 2) how many bars before H1's earliest (2020)?
    old = mt5.copy_rates_range(
        SYM, tf, datetime(2020, 1, 1, tzinfo=UTC), datetime(2020, 1, 15, tzinfo=UTC)
    )
    # 3) full range probe
    full = mt5.copy_rates_range(SYM, tf, datetime(2009, 1, 1, tzinfo=UTC), datetime.now(UTC))
    rc = f"recent={0 if recent is None else len(recent)}"
    if recent is not None and len(recent):
        rc += f" latest={datetime.fromtimestamp(int(recent[-1]['time']), tz=UTC).isoformat()}"
        rc += f" first_recent={datetime.fromtimestamp(int(recent[0]['time']), tz=UTC).isoformat()}"
    oc = f"old2020={0 if old is None else len(old)}"
    if old is not None and len(old):
        rc += f" old_start={datetime.fromtimestamp(int(old[0]['time']), tz=UTC).isoformat()}"
    fc = f"full={0 if full is None else len(full)}"
    if full is not None and len(full):
        fc += f" from={datetime.fromtimestamp(int(full[0]['time']), tz=UTC).isoformat()}"
    print(f"{name}: {rc} | {oc} | {fc}")

mt5.shutdown()
print("done")

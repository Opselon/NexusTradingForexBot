"""Diagnose copy_rates_from_pos failure with different counts (read-only)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import MetaTrader5 as mt5

if not mt5.initialize():
    print("FATAL init:", mt5.last_error())
    sys.exit(1)

SYM = "XAUUSD"
for count in (3, 100, 1000, 5000, 10000, 50000, 100000):
    r = mt5.copy_rates_from_pos(SYM, mt5.TIMEFRAME_M1, 0, count)
    n = 0 if r is None else len(r)
    err = mt5.last_error()
    first = None
    last = None
    if r is not None and len(r):
        first = datetime.fromtimestamp(int(r[0]["time"]), tz=UTC).isoformat()
        last = datetime.fromtimestamp(int(r[-1]["time"]), tz=UTC).isoformat()
    print(f"M1 count={count}: returned={n} first={first} last={last} last_error={err}")

# also test M5 with 100k
r = mt5.copy_rates_from_pos(SYM, mt5.TIMEFRAME_M5, 0, 100000)
print(f"M5 count=100000: returned={0 if r is None else len(r)} last_error={mt5.last_error()}")

# test smaller pos step
r = mt5.copy_rates_from_pos(SYM, mt5.TIMEFRAME_M1, 50000, 100000)
print(
    f"M1 pos=50000 count=100000: returned={0 if r is None else len(r)} last_error={mt5.last_error()}"
)

mt5.shutdown()
print("done")

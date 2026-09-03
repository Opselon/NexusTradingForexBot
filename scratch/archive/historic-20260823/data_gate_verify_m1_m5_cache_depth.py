"""Verify true M1/M5 cache depth with deep from_pos offsets (read-only)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import MetaTrader5 as mt5

if not mt5.initialize():
    print("FATAL init:", mt5.last_error())
    sys.exit(1)

for sym, tf, tfname in (
    ("XAUUSD", mt5.TIMEFRAME_M1, "M1"),
    ("XAUUSD", mt5.TIMEFRAME_M5, "M5"),
    ("XAUUSD", mt5.TIMEFRAME_M15, "M15"),
):
    for pos in (100000, 150000, 200000, 300000, 500000, 1000000):
        r = mt5.copy_rates_from_pos(sym, tf, pos, 50000)
        n = 0 if r is None else len(r)
        first_t = None
        if r is not None and len(r):
            first_t = datetime.fromtimestamp(int(r[0]["time"]), tz=UTC).isoformat()
        print(f"{tfname} pos={pos}: returned={n} first={first_t} err={mt5.last_error()}")
        if n == 0:
            break

mt5.shutdown()
print("done")

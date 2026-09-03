"""
Verify clock sources:
1) MT5 deal timestamps come from datetime.fromtimestamp(d.time, tz=UTC) (adapter)
2) Engine 'now' comes from datetime.now(UTC) (wall clock)
3) Check current host time vs MT5 server time to detect skew.
"""

from datetime import UTC, datetime

import MetaTrader5 as mt5

ok = mt5.initialize()
if not ok:
    print("init failed", mt5.last_error())
    raise SystemExit(1)

host_now = datetime.now(UTC)
try:
    # terminal time from a fresh deal
    import time as _time

    deals = mt5.history_deals_get(_time.time() - 86400, _time.time()) or []
    if deals:
        d = deals[-1]
        deal_ts = datetime.fromtimestamp(d.time, tz=UTC)
        print(f"host now (UTC):            {host_now.isoformat()}")
        print(f"last deal time (UTC):      {deal_ts.isoformat()}")
        print(f"delta (host - deal):       {(host_now - deal_ts).total_seconds() / 3600:.3f} h")
    else:
        print("no deals in window")
except Exception as e:
    print("error:", e)

# server time via symbol tick time
tick = mt5.symbol_info_tick("XAUUSD")
if tick:
    tick_ts = datetime.fromtimestamp(tick.time, tz=UTC)
    print(f"last tick time (UTC):      {tick_ts.isoformat()}")
    print(f"tick age:                  {(host_now - tick_ts).total_seconds():.1f} s")
mt5.shutdown()

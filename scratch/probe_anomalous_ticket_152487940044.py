"""Inspect the anomalous ticket 152487940044 deal set."""

import sys
from datetime import UTC, datetime, timedelta

import MetaTrader5 as mt5

ok = mt5.initialize()
if not ok:
    print("init failed", mt5.last_error())
    sys.exit(1)

now = datetime.now(UTC)
deals = mt5.history_deals_get(now - timedelta(hours=48), now, group="XAUUSD") or []
ticket = 152487940044
td = [d for d in deals if d.position_id == ticket]
print(f"deals for {ticket}: {len(td)}")
for d in td:
    print(
        f"  t={d.ticket} entry={'IN' if d.entry == 0 else 'OUT'} type={'BUY' if d.type == 0 else 'SELL'} "
        f"vol={d.volume} price={d.price} profit={d.profit:+.2f} "
        f"time={datetime.fromtimestamp(d.time, tz=UTC)} comment={d.comment}"
    )
mt5.shutdown()

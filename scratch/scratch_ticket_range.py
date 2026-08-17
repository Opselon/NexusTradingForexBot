"""
Resolve the ticket-number question: which broker positions have ticket
numbers 1524878..1524884 (the engine-tracked ones)?
"""

from datetime import UTC, datetime

import MetaTrader5 as mt5

ok = mt5.initialize()
if not ok:
    print("init failed", mt5.last_error())
    raise SystemExit(1)

# history orders in the FULL range including phantom tickets
horders = mt5.history_orders_get(datetime(2026, 8, 16, 12, 0, tzinfo=UTC), datetime.now(UTC)) or []
print("total history orders (16h to now):", len(horders))
rng = [o for o in horders if 152486500000 <= o.ticket <= 152489000000]
print("orders in 1524865-1524890 range:", len(rng))
for o in sorted(rng, key=lambda x: x.time_setup):
    print(
        f"  order={o.ticket} type={o.type} vol={o.volume_initial} price={o.price_open} "
        f"state={o.state} setup={datetime.fromtimestamp(o.time_setup, tz=UTC)} "
        f"done={datetime.fromtimestamp(o.time_done, tz=UTC)} comment={o.comment}"
    )

# deals too
now_utc = datetime.now(UTC)
deals = mt5.history_deals_get(datetime(2026, 8, 16, 12, 0), now_utc) or []
print("\ntotal deals:", len(deals))
drng = [
    d
    for d in deals
    if 152486500000 <= d.position_id <= 152489000000 or 152486500000 <= d.ticket <= 152489000000
]
print("deals in range:", len(drng))
for d in sorted(drng, key=lambda x: x.time):
    print(
        f"  deal={d.ticket} order={d.order} pos={d.position_id} entry={'IN' if d.entry == 0 else 'OUT'} "
        f"profit={d.profit} time={datetime.fromtimestamp(d.time, tz=UTC)} comment={d.comment}"
    )
mt5.shutdown()

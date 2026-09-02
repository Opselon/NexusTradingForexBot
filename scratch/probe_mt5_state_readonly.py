"""Read-only MT5 probe (no orders placed). Verifies current broker state + cancels never sent."""

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, ".")
import MetaTrader5 as mt5


def log(msg: str) -> None:
    print(msg, flush=True)


log(f"=== MT5 READ-ONLY PROBE {datetime.now(UTC).isoformat()} ===")

if not mt5.initialize():
    log(f"initialize FAILED: {mt5.last_error()}")
    sys.exit(1)

ti = mt5.terminal_info()
log(
    f"terminal: {getattr(ti, 'name', '?')} | connected={getattr(ti, 'connected', '?')} "
    f"| trade_allowed={getattr(ti, 'trade_allowed', '?')}"
)
ai = mt5.account_info()
log(
    f"account: login={getattr(ai, 'login', '?')} server={getattr(ai, 'server', '?')} "
    f"balance={getattr(ai, 'balance', 0):.2f} equity={getattr(ai, 'equity', 0):.2f}"
)

# 1. ALL pending orders (unfiltered) — the source of truth
orders = mt5.orders_get()
if orders is None:
    log(f"orders_get -> None | last_error={mt5.last_error()}")
else:
    log(f"orders_get total={len(orders)}")
    for o in orders:
        log(
            f"  ORDER ticket={o.ticket} sym={o.symbol} type={o.type} state={o.state} "
            f"magic={o.magic} vol={o.volume_current:.2f} price={o.price_open} "
            f"sl={o.sl} tp={o.tp} setup={o.time_setup} comment={o.comment}"
        )

# 2. Bot positions
pos = mt5.positions_get()
if pos is None:
    log(f"positions_get -> None | last_error={mt5.last_error()}")
else:
    log(f"positions_get total={len(pos)}")
    for p in pos:
        log(
            f"  POS ticket={p.ticket} sym={p.symbol} type={p.type} magic={p.magic} "
            f"vol={p.volume:.2f} price={p.price_open} sl={p.sl} tp={p.tp} profit={p.profit:.2f}"
        )

# 3. History orders for the last 12h (bounded) — check the forensic tickets
now = datetime.now(UTC)
h_from = now - timedelta(hours=12)
hist = mt5.history_orders_get(h_from, now)
if hist is None:
    log(f"history_orders_get -> None | last_error={mt5.last_error()}")
else:
    log(f"history_orders_get total={len(hist)} ({h_from.isoformat()}..{now.isoformat()})")
    targets = {152495362150, 152495369729, 152495564091, 152495090247, 152495088791}
    for o in hist:
        if o.ticket in targets or (getattr(o, "magic", 0) == 888101 and o.time_setup > 1786950000):
            log(
                f"  HIST ticket={o.ticket} sym={o.symbol} type={o.type} state={o.state} "
                f"magic={o.magic} vol={o.volume_initial:.2f} price={o.price_open} "
                f"setup={o.time_setup} done={o.time_done} comment={o.comment}"
            )

mt5.shutdown()
log("=== PROBE COMPLETE ===")

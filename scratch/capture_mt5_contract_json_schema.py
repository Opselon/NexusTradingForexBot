"""READ-ONLY MT5 response contract capture.

Captures the REAL JSON-safe structures returned by the installed MetaTrader5
package for every accounting-relevant API, and serializes each object field by
field as {field_name, type, value, nullable, source}.

NEVER places orders. NEVER sends trade requests. Read-only getters only.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from typing import Any

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import MetaTrader5 as mt5

OUT = "tests/fixtures/mt5"


def field_map(obj: Any, source: str = "MT5") -> dict[str, dict[str, Any]]:
    """Serialize every attribute of an MT5 namedtuple-like into a JSON-safe
    {field_name: {type, value, nullable, source}} representation."""
    out: dict[str, dict[str, Any]] = {}
    if obj is None:
        return {"_none": {"type": "None", "value": None, "nullable": True, "source": source}}
    keys = getattr(obj, "_fields", None)
    names = keys if keys else [k for k in dir(obj) if not k.startswith("_")]
    for name in names:
        try:
            val = getattr(obj, name)
        except Exception as exc:  # pragma: no cover
            out[name] = {
                "type": "ERROR",
                "value": str(exc),
                "nullable": True,
                "source": source,
            }
            continue
        if isinstance(val, datetime):
            out[name] = {
                "type": "datetime",
                "value": val.isoformat(),
                "nullable": val is None,
                "source": source,
            }
        elif hasattr(val, "_fields"):
            out[name] = {
                "type": f"namedtuple:{type(val).__name__}",
                "value": {k: v for k, v in val._asdict().items()},
                "nullable": False,
                "source": source,
            }
        else:
            out[name] = {
                "type": type(val).__name__,
                "value": val,
                "nullable": val is None,
                "source": source,
            }
    return out


def serialize_payload(label: str, payload: dict[str, Any]) -> None:
    path = f"{OUT}/{label}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"[OK] {path} ({len(payload)} top-level keys)")


def main() -> int:
    import os

    os.makedirs(OUT, exist_ok=True)

    print("=== MT5 CONNECT (read-only) ===")
    if not mt5.initialize():
        print("FATAL: mt5.initialize() failed:", mt5.last_error())
        return 1
    print("MT5 version:", mt5.version())

    now = datetime.now(UTC)
    day_ago = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # 1. account_info
    acc = mt5.account_info()
    serialize_payload(
        "account_info",
        {"operation": "account_info", "captured_at": now.isoformat(), "object": field_map(acc)},
    )

    # 2. terminal_info
    term = mt5.terminal_info()
    serialize_payload(
        "terminal_info",
        {"operation": "terminal_info", "captured_at": now.isoformat(), "object": field_map(term)},
    )

    sym = "XAUUSD"
    # 3. symbol_info
    si = mt5.symbol_info(sym)
    serialize_payload(
        "xauusd_symbol",
        {
            "operation": "symbol_info",
            "symbol": sym,
            "captured_at": now.isoformat(),
            "object": field_map(si),
        },
    )

    # 4. symbol_info_tick
    tick = mt5.symbol_info_tick(sym)
    serialize_payload(
        "xauusd_tick",
        {
            "operation": "symbol_info_tick",
            "symbol": sym,
            "captured_at": now.isoformat(),
            "object": field_map(tick),
        },
    )

    # 5. positions_get (ALL account positions, no filter)
    pos = mt5.positions_get()
    serialize_payload(
        "positions",
        {
            "operation": "positions_get",
            "captured_at": now.isoformat(),
            "count": 0 if pos is None else len(pos),
            "objects": [field_map(p) for p in (pos or [])],
        },
    )

    # 6. orders_get (active pending orders)
    ords = mt5.orders_get()
    serialize_payload(
        "orders",
        {
            "operation": "orders_get",
            "captured_at": now.isoformat(),
            "count": 0 if ords is None else len(ords),
            "objects": [field_map(o) for o in (ords or [])],
        },
    )

    # 7-8. history_orders_get / history_deals_get (UTC day window, no symbol filter)
    h_orders = mt5.history_orders_get(day_ago, now)
    h_deals = mt5.history_deals_get(day_ago, now)
    serialize_payload(
        "history_orders",
        {
            "operation": "history_orders_get",
            "from": day_ago.isoformat(),
            "to": now.isoformat(),
            "captured_at": now.isoformat(),
            "count": 0 if h_orders is None else len(h_orders),
            "objects": [field_map(o) for o in (h_orders or [])],
        },
    )
    serialize_payload(
        "history_deals",
        {
            "operation": "history_deals_get",
            "from": day_ago.isoformat(),
            "to": now.isoformat(),
            "captured_at": now.isoformat(),
            "count": 0 if h_deals is None else len(h_deals),
            "objects": [field_map(d) for d in (h_deals or [])],
        },
    )

    # 9. copy_rates_from_pos M1
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 5)
    rates_payload = []
    if rates is not None:
        for row in rates:
            rates_payload.append(
                {
                    "time": int(row["time"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "tick_volume": int(row["tick_volume"]),
                    "spread": int(row["spread"]),
                    "real_volume": int(row["real_volume"]),
                    "time_utc": datetime.fromtimestamp(int(row["time"]), tz=UTC).isoformat(),
                }
            )
    serialize_payload(
        "xauusd_m1_rates",
        {
            "operation": "copy_rates_from_pos",
            "symbol": sym,
            "timeframe": "M1",
            "count_requested": 5,
            "count_returned": 0 if rates is None else len(rates),
            "captured_at": now.isoformat(),
            "objects": rates_payload,
        },
    )

    # 10-11. order_calc_profit / order_calc_margin (POSITIONAL ONLY, read-only calc)
    buy = mt5.ORDER_TYPE_BUY
    calc = {}
    try:
        profit = mt5.order_calc_profit(buy, sym, 0.01, 2000.0, 2001.5)
        calc["order_calc_profit"] = {
            "ok": profit is not None,
            "value": profit,
            "args": "positional (0, XAUUSD, 0.01, 2000.0, 2001.5)",
        }
    except Exception as exc:
        calc["order_calc_profit"] = {"ok": False, "error": str(exc)}
    try:
        margin = mt5.order_calc_margin(buy, sym, 0.01, 2000.0)
        calc["order_calc_margin"] = {"ok": margin is not None, "value": margin}
    except Exception as exc:
        calc["order_calc_margin"] = {"ok": False, "error": str(exc)}
    serialize_payload(
        "order_calc",
        {"operation": "order_calc_*", "symbol": sym, "captured_at": now.isoformat(), **calc},
    )

    mt5.shutdown()
    print("\n=== DONE (read-only, no orders placed) ===")
    return 0


if __name__ == "__main__":
    t0 = time.perf_counter()
    rc = main()
    print(f"elapsed={time.perf_counter() - t0:.1f}s")
    sys.exit(rc)

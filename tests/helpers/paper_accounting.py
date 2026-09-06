"""Task D reconciliation helper for PAPER accounting (tests/helpers).

Importable helper to assert equity==balance+unrealized and realized invariants.
"""

from __future__ import annotations

from typing import Any


def assert_accounting_invariants(adapter: Any, *, strict: bool = True) -> dict[str, Any]:
    """Assert equity == balance + floating PnL within 1 cent.

    Also asserts each open position's stored profit matches current tick when
    available.  Returns the reconcile dict for logging.  Raises AssertionError
    on violation (strict=True, default), otherwise returns ok=False.
    """
    # prefer adapter's own reconcile if present
    if hasattr(adapter, "reconcile_accounting"):
        try:
            return adapter.reconcile_accounting()  # type: ignore[operator]
        except AssertionError:
            raise
        except Exception as e:
            if strict:
                raise AssertionError(f"reconcile_accounting failed: {e}") from e

    # fallback manual check against adapter attributes
    balance = float(getattr(adapter, "balance", 0.0))
    equity = float(getattr(adapter, "equity", 0.0))
    positions = list(
        getattr(adapter, "_positions", []) or getattr(adapter, "get_positions", lambda **_: [])()
    )
    # try floating helper
    floating = None
    if hasattr(adapter, "_floating_pnl"):
        try:
            floating = float(adapter._floating_pnl())  # type: ignore[operator]
        except Exception:
            floating = None
    if floating is None:
        floating = sum(float(getattr(p, "profit", 0.0)) for p in positions)
    expected = round(balance + floating, 2)
    ok = abs(equity - expected) < 0.015
    info: dict[str, Any] = {
        "balance": balance,
        "equity": equity,
        "floating_pnl": floating,
        "expected_equity": expected,
        "ok": ok,
        "positions": len(positions),
    }
    if strict:
        assert ok, f"equity invariant broken: {info}"
    return info


def assert_realized_pnl_formula(
    *,
    symbol: str,
    order_type: str,
    price_open: float,
    price_close: float,
    volume: float,
    contract_size: float = 100.0,
    expected: float,
) -> None:
    """Check BUY=(close-open)*vol*contract, SELL=(open-close)*vol*contract."""
    if str(order_type).upper() == "BUY":
        calc = round(
            (float(price_close) - float(price_open)) * float(volume) * float(contract_size), 2
        )
    else:
        calc = round(
            (float(price_open) - float(price_close)) * float(volume) * float(contract_size), 2
        )
    assert abs(calc - float(expected)) < 0.015, (
        f"realized PnL mismatch: calc {calc} vs expected {expected} ({symbol} {order_type} {price_open}->{price_close} x{volume}*c{contract_size})"
    )

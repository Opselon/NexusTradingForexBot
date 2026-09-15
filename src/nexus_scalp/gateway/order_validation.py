"""Gateway order-request payload validation (BUG-293, lane-11 L11-6).

Fail-closed structural contract for the order-writing actions of
``gateway/server.py`` (SEND_ORDER, EXECUTE_MARKET_ORDER, PLACE_PENDING_ORDER).

Before this module the server coerced malformed order inputs into SILENT
DEFAULTS — ``float(payload.get("volume") or 0.01)`` and
``float(payload.get("price") or 0.0)`` — so a request that lost its volume
still executed at minimum lot size, and non-finite values (JSON parses
``NaN``/``Infinity`` cleanly), negative prices, or an empty symbol were
forwarded to the broker untouched; the market path performed no structural
validation at all (unlike the pending path, whose broker-truth gate lives in
``DirectMT5Adapter._validate_pending_request``).

This is the boundary half of that contract: every malformed payload is
rejected BEFORE any adapter dispatch, mirroring the audited
``(ok, reasons)`` shape of ``_validate_pending_request`` — the server answers
``{"status": "FAILED", "message": "invalid order request", "reasons": [...]}``
(HTTP 200, the wire shape the client maps to its typed REJECTED tri-state;
never a silent coercion, never an exception-to-500).

Wire-contract notes:
  * ``price == 0.0`` is the documented "execute at market" sentinel on the
    market/pending transport (RemoteMT5GatewayAdapter.send_* always fills it
    from the already-validated domain model); the legacy TradeOrder path
    (SEND_ORDER) requires strictly positive prices because the domain model
    itself does (domain/models.py TradeOrder volume/price/stop_loss/
    take_profit gt=0).
  * stop_loss/take_profit of 0.0 mean "no stop" on the market/pending path;
    on the legacy path they are required and must be > 0.
  * booleans are rejected: bool is an int subclass in Python, and an order
    volume of ``true`` is a client defect, not 1.0 lots (AGENT-18
    isinstance-bool precedent in configuration/runtime_config.py).
"""

from __future__ import annotations

import math
from typing import Any


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_field(
    label: str,
    raw: Any,
    reasons: list[str],
    *,
    required: bool,
    positive: bool,
) -> float | None:
    """Validate one numeric field value; append reason codes on failure.

    Returns the parsed float, or None when absent/None (only possible for
    non-required fields) or failed validation.
    """
    if raw is None:
        if required:
            reasons.append(f"missing_{label}")
        return None
    if not _is_number(raw):
        reasons.append(f"{label}_not_numeric")
        return None
    value = float(raw)
    if not math.isfinite(value):
        reasons.append(f"{label}_not_finite")
        return None
    if value < 0.0:
        reasons.append(f"{label}_negative")
        return None
    if positive and value == 0.0:
        reasons.append(f"{label}_not_positive")
        return None
    return value


def validated_order_request(
    payload: dict[str, Any], *, legacy_trade_order: bool = False
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate an order-write payload WITHOUT dispatching anything.

    Returns ``(request, [])`` with normalized floats on success, or
    ``(None, reasons)`` with a non-empty reason list on failure (mirrors
    ``_validate_pending_request``'s ``(False, reasons)`` shape). No default
    is ever invented for a missing field — a lost ``volume`` fails closed,
    it does not become 0.01 lots.
    """
    reasons: list[str] = []

    symbol = payload.get("symbol")
    if symbol is None:
        reasons.append("missing_symbol")
    elif not isinstance(symbol, str) or not symbol.strip():
        reasons.append("empty_symbol")

    if payload.get("order_type") is None and payload.get("type") is None:
        # Direction must never be defaulted: a missing order_type silently
        # becoming BUY is the same fail-open class as the volume default.
        reasons.append("missing_order_type")

    volume = _validate_field("volume", payload.get("volume"), reasons, required=True, positive=True)
    price = _validate_field(
        "price", payload.get("price"), reasons, required=True, positive=legacy_trade_order
    )
    sl_raw = payload.get("stop_loss") if payload.get("stop_loss") is not None else payload.get("sl")
    tp_raw = (
        payload.get("take_profit") if payload.get("take_profit") is not None else payload.get("tp")
    )
    stop_loss = _validate_field(
        "stop_loss", sl_raw, reasons, required=legacy_trade_order, positive=legacy_trade_order
    )
    take_profit = _validate_field(
        "take_profit", tp_raw, reasons, required=legacy_trade_order, positive=legacy_trade_order
    )

    if reasons or volume is None or price is None:
        return None, reasons
    return (
        {
            "symbol": str(symbol).strip(),
            "volume": float(volume),
            "price": float(price),
            "stop_loss": float(stop_loss) if stop_loss is not None else 0.0,
            "take_profit": float(take_profit) if take_profit is not None else 0.0,
        },
        [],
    )

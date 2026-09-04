"""
Ledger Row -> Canonical TradeRecord Normalization
=================================================
The ONE place a raw `audit_ledger` row becomes accounting truth.

Two rules drive everything here:

1. WIN/LOSS IS DECIDED BY MONEY. A stop that had been moved to breakeven is
   still a stop-out; it must never be reclassified as a win. `outcome` derives
   from net PnL only, while `exit_classification` and `risk_free_state` stay
   independently visible so the two facts never get conflated.

2. NO IMPUTED NUMBERS. When the risk basis cannot be reconstructed from stored
   evidence, `realized_r` / `risk_usd` are None rather than 0.0, because a 0.0 R
   is indistinguishable from a genuine scratch trade.
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.accounting.models import (
    ExitClassification,
    TradeOutcome,
    TradeRecord,
)
from nexus_scalp.accounting.periods import parse_sql_timestamp

#: Net PnL inside +/- this band counts as a scratch, not a win or a loss.
BREAKEVEN_USD_EPSILON = 0.01

#: Raw `ExitMechanism` -> canonical classification. Stop mechanisms are refined
#: further by `_classify_stop` using the actual SL geometry.
_MECHANISM_MAP: dict[str, ExitClassification] = {
    "TAKE_PROFIT_HIT": ExitClassification.TAKE_PROFIT,
    "HARD_SL_HIT": ExitClassification.INITIAL_STOP,
    "RISK_FREE_SL_HIT": ExitClassification.BREAKEVEN_STOP,
    "BE_HIT": ExitClassification.BREAKEVEN_STOP,
    "BREAK_EVEN_SL_HIT": ExitClassification.BREAKEVEN_STOP,
    "TRAILING_STOP_HIT": ExitClassification.TRAILING_STOP,
    "AI_REVERSAL_EXIT": ExitClassification.STRATEGY_EXIT,
    "HOLD_SCORE_DECAY": ExitClassification.STRATEGY_EXIT,
    "MANUAL_CLOSE": ExitClassification.MANUAL_EXIT,
    "PROFIT_GIVEBACK_PROTECTION": ExitClassification.EMERGENCY_EXIT,
    "SYSTEM_CLOSE": ExitClassification.STRATEGY_EXIT,
    "RECONCILIATION_CLOSE": ExitClassification.STRATEGY_EXIT,
    "BROKER_CLOSE": ExitClassification.STRATEGY_EXIT,
}


def _f(row: dict[str, Any], *names: str, default: float = 0.0) -> float:
    """Reads the first present numeric column, tolerating schema drift."""
    for name in names:
        if not isinstance(name, str):
            continue
        try:
            present = name in row
        except TypeError:
            # hash-unhashable-value: name is unhashable (e.g. dict)
            continue
        if not present or row[name] is None:
            continue
        try:
            return float(row[name])
        except (TypeError, ValueError):
            continue
    return default


def _s(row: dict[str, Any], *names: str) -> str:
    """Reads the first present non-empty text column."""
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def _is_long(direction: str) -> bool:
    return "BUY" in direction.upper() or direction.upper() in ("LONG", "0")


def _classify_stop(
    *,
    is_long: bool,
    entry: float,
    initial_sl: float,
    final_sl: float,
    was_sl_modified: bool,
    risk_free_flag: bool,
    point_tolerance: float,
) -> ExitClassification:
    """
    Distinguishes INITIAL / BREAKEVEN / TRAILING stop-outs from SL geometry.

    A stop that never moved is INITIAL. A stop parked at (approximately) the
    entry price is BREAKEVEN. A stop pushed strictly beyond entry in the
    favourable direction is TRAILING. This separation matters because all three
    look identical in PnL terms but represent very different management quality.
    """
    if not (was_sl_modified or risk_free_flag):
        return ExitClassification.INITIAL_STOP
    if final_sl <= 0.0 or entry <= 0.0:
        return (
            ExitClassification.BREAKEVEN_STOP if risk_free_flag else ExitClassification.INITIAL_STOP
        )

    delta = (final_sl - entry) if is_long else (entry - final_sl)
    if delta > point_tolerance:
        return ExitClassification.TRAILING_STOP
    if delta >= -point_tolerance:
        return ExitClassification.BREAKEVEN_STOP
    # SL was modified but still sits at a loss relative to entry: it is a
    # tightened protective stop, not a breakeven.
    if initial_sl > 0.0 and abs(final_sl - initial_sl) > point_tolerance:
        return ExitClassification.TRAILING_STOP
    return ExitClassification.INITIAL_STOP


def classify_exit(row: dict[str, Any]) -> tuple[ExitClassification, bool]:
    """
    Determines how a position ended and whether it reached a risk-free state.

    Returns:
        (classification, risk_free_state)
    """
    mechanism = _s(row, "exit_mechanism").upper()
    direction = _s(row, "direction")
    is_long = _is_long(direction)

    entry = _f(row, "entry_price", "open_price")
    initial_sl = _f(row, "initial_sl_price")
    final_sl = _f(row, "final_sl_price")
    was_modified = bool(int(_f(row, "was_sl_modified")))
    risk_free_flag = bool(int(_f(row, "is_risk_free_hit")))

    # Tolerance scaled off the risk distance so gold (2 digits) and FX (5 digits)
    # both behave sensibly without hard-coding a point size.
    risk_distance = abs(entry - initial_sl) if (entry > 0.0 and initial_sl > 0.0) else 0.0
    tolerance = max(risk_distance * 0.02, 1e-6)

    risk_free_state = risk_free_flag or (
        was_modified
        and final_sl > 0.0
        and entry > 0.0
        and ((final_sl - entry) if is_long else (entry - final_sl)) >= -tolerance
    )

    base = _MECHANISM_MAP.get(mechanism)

    if base is not None and base.is_stop_exit:
        return (
            _classify_stop(
                is_long=is_long,
                entry=entry,
                initial_sl=initial_sl,
                final_sl=final_sl,
                was_sl_modified=was_modified,
                risk_free_flag=risk_free_flag,
                point_tolerance=tolerance,
            ),
            risk_free_state,
        )

    if base is not None:
        return base, risk_free_state

    if mechanism:
        # Unknown but present mechanism: keep it visible as OTHER rather than
        # silently folding it into a stop or a manual exit.
        return ExitClassification.OTHER_EXIT, risk_free_state

    status = _s(row, "status").upper()
    if "PARTIAL" in status:
        return ExitClassification.PARTIAL_CLOSE, risk_free_state
    return ExitClassification.UNKNOWN, risk_free_state


def classify_outcome(net_pnl: float) -> TradeOutcome:
    """Financial result from realized money alone."""
    if net_pnl > BREAKEVEN_USD_EPSILON:
        return TradeOutcome.WIN
    if net_pnl < -BREAKEVEN_USD_EPSILON:
        return TradeOutcome.LOSS
    return TradeOutcome.BREAKEVEN


def _value_per_point(row: dict[str, Any]) -> float | None:
    """
    Derives USD per price-point from the stored excursion pair.

    `MAE_usd / mae` (or the MFE equivalent) gives the money value of one price
    point for this exact position size, without needing symbol specs at report
    time. Returns None when neither pair is usable.
    """
    for pts_key, usd_key in (("mae", "MAE_usd"), ("mfe", "MFE_usd")):
        pts = abs(_f(row, pts_key))
        usd = abs(_f(row, usd_key))
        if pts > 1e-9 and usd > 1e-9:
            return usd / pts
    return None


def reconstruct_risk(row: dict[str, Any], net_pnl: float) -> tuple[float | None, float | None]:
    """
    Reconstructs (risk_usd, realized_r) from stored evidence.

    Requires an initial stop AND a usable point value; returns (None, None)
    otherwise so downstream R statistics simply exclude the trade instead of
    averaging in a fabricated zero.
    """
    entry = _f(row, "entry_price", "open_price")
    initial_sl = _f(row, "initial_sl_price")
    if entry <= 0.0 or initial_sl <= 0.0:
        return None, None

    per_point = _value_per_point(row)
    if per_point is None:
        return None, None

    risk_usd = abs(entry - initial_sl) * per_point
    if risk_usd <= 1e-9:
        return None, None
    return risk_usd, net_pnl / risk_usd


def normalize_trade_row(row: dict[str, Any]) -> TradeRecord:
    """
    Converts one raw `audit_ledger` row into the canonical `TradeRecord`.

    Net PnL is computed here exactly once (gross minus commission minus swap,
    both treated as costs) and stored, so no consumer can re-derive it with a
    different sign convention. A persisted `net_pnl_usd` is trusted when present
    because `log_ledger_closed` already applied the same rule.
    """
    gross = _f(row, "gross_pnl_usd", "pnl")
    commission = abs(_f(row, "commission"))
    swap = _f(row, "swap")

    persisted_net = row.get("net_pnl_usd")
    if persisted_net is not None and abs(float(persisted_net)) > 1e-12:
        net = float(persisted_net)
    else:
        net = gross - commission - swap

    classification, risk_free = classify_exit(row)
    risk_usd, realized_r = reconstruct_risk(row, net)

    duration = _f(row, "duration_seconds", "duration_sec")
    confidence = row.get("ai_confidence_at_open")

    return TradeRecord(
        ticket=int(_f(row, "ticket")),
        symbol=_s(row, "symbol"),
        direction=_s(row, "direction"),
        volume=_f(row, "volume"),
        entry_price=_f(row, "entry_price", "open_price"),
        exit_price=_f(row, "exit_price", "close_price"),
        gross_pnl=gross,
        commission=commission,
        swap=swap,
        net_pnl=net,
        opened_at=parse_sql_timestamp(_s(row, "open_time")),
        closed_at=parse_sql_timestamp(_s(row, "close_time", "timestamp")),
        duration_sec=duration,
        exit_mechanism_raw=_s(row, "exit_mechanism"),
        exit_classification=classification,
        outcome=classify_outcome(net),
        risk_free_state=risk_free,
        was_sl_modified=bool(int(_f(row, "was_sl_modified"))),
        initial_sl=_f(row, "initial_sl_price"),
        final_sl=_f(row, "final_sl_price"),
        mae_points=_f(row, "mae"),
        mfe_points=_f(row, "mfe"),
        mae_usd=_f(row, "MAE_usd"),
        mfe_usd=_f(row, "MFE_usd"),
        status=_s(row, "status"),
        order_id=_s(row, "order_id"),
        entry_reason=_s(row, "entry_reason"),
        confidence_at_open=float(confidence) if confidence not in (None, "") else None,
        regime_at_open=_s(row, "market_regime_at_open"),
        balance_after=_f(row, "account_balance_after") or None,
        equity_after=_f(row, "account_equity_after") or None,
        realized_r=realized_r,
        risk_usd=risk_usd,
    )

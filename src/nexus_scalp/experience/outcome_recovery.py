"""Phase 14 outcome correlation & broker-close reconstruction (Experience)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.experience.models import (
    BREAKEVEN_R_BAND,
    BrokerOutcome,
    ExitReason,
    ExperienceRecord,
    OutcomeClass,
    OutcomeCorrelationSource,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.experience.outcome_recovery")


def classify_outcome_class(realized_r: float) -> OutcomeClass:
    """
    Deterministic first-class outcome classification.

    BREAK_EVEN is a REAL outcome class, never "skip learning because PnL ~ 0".
    The band mirrors the evaluator's win/loss thresholds so per-strategy
    counts and per-outcome classes always agree.
    """
    if realized_r > BREAKEVEN_R_BAND:
        return OutcomeClass.WIN
    if realized_r < -BREAKEVEN_R_BAND:
        return OutcomeClass.LOSS
    return OutcomeClass.BREAK_EVEN


def is_protective_exit(exit_reason: str) -> bool:
    """
    True when the exit mechanism is a protective stop / trailing / risk-free
    closure rather than a genuine manual closure.
    """
    reason = (exit_reason or "").upper()
    if reason in {"", "UNKNOWN", "MANUAL_CLOSE", "SYSTEM_CLOSE", "RECONCILIATION_CLOSE"}:
        return False
    if "SL" in reason or "STOP" in reason or "RISK_FREE" in reason or "TRAIL" in reason:
        return True
    return False


def classify_exit_reason(
    *,
    deal_reason_code: int,
    comment: str,
    profit_usd: float,
    exit_price: float,
    tp_price: float,
    sl_price: float,
    final_sl: float,
    entry_price: float,
    was_sl_modified: bool,
    direction: str,
    forced_mechanism: str | None = None,
) -> str:
    """
    Maps broker deal evidence to the canonical exit taxonomy.

    Priority: engine-forced mechanism > broker DEAL_REASON + protective
    context (SL moved to break-even or trailing) > generic heuristics.

    NEVER labels a broker stop-out (SL / break-even / trailing) as
    MANUAL_CLOSE merely because the internal state machine performed
    protection logic before the broker close event.
    """
    if forced_mechanism:
        return forced_mechanism

    reason = int(deal_reason_code or 0)
    comment_l = (comment or "").lower()
    is_buy = "BUY" in str(direction).upper()
    near_sl = sl_price > 0.0 and abs(exit_price - sl_price) < 0.15
    near_tp = tp_price > 0.0 and abs(exit_price - tp_price) < 0.10

    if near_tp or reason == 4 or "tp" in comment_l:
        return ExitReason.TAKE_PROFIT_HIT

    # Engine protective force exits are authoritative.

    # BE geometry wins over trailing comments: a stop parked at/above entry
    # (within the BE lock buffer) is a break-even lock, no matter what the
    # engine's trailing comment says ("SL hit NSE_TRAIL" at entry = BE).
    # A stop strictly beyond entry + BE_TOLERANCE is a genuine trailing lock.
    be_tolerance = max(0.5, (entry_price * 0.0005) if entry_price > 0.0 else 0.5)
    if (near_sl or "sl" in comment_l) and final_sl > 0.0 and entry_price > 0.0:
        within_be = entry_price - be_tolerance <= final_sl <= entry_price + be_tolerance
        if within_be:
            if was_sl_modified:
                return ExitReason.BREAK_EVEN_SL_HIT
            return ExitReason.RISK_FREE_SL_HIT
        # Strictly beyond entry: trailing lock.
        if (is_buy and final_sl > entry_price) or (not is_buy and final_sl < entry_price):
            return ExitReason.TRAILING_STOP_HIT

    # Genuine trailing: protective stop strictly beyond entry (locked profit).
    if "trail" in comment_l or comment_l.startswith("nse_trail"):
        if final_sl > 0.0 and entry_price > 0.0:
            if (is_buy and final_sl > entry_price) or (not is_buy and final_sl < entry_price):
                return ExitReason.TRAILING_STOP_HIT
        return ExitReason.TRAILING_STOP_HIT

    if near_sl or reason == 3 or "sl" in comment_l:
        return ExitReason.HARD_SL_HIT

    if reason == 1:
        # Real MT5 client manual close, no protective evidence.
        return ExitReason.MANUAL_CLOSE

    if profit_usd > 0.0:
        return ExitReason.TP_HIT if near_tp else ExitReason.SYSTEM_CLOSE
    if profit_usd < 0.0:
        return ExitReason.SL_HIT if near_sl else ExitReason.SYSTEM_CLOSE

    if was_sl_modified and final_sl > 0.0 and entry_price > 0.0:
        if (is_buy and final_sl >= entry_price) or (not is_buy and final_sl <= entry_price):
            return ExitReason.BREAK_EVEN_SL_HIT

    return ExitReason.UNKNOWN


def reconstruct_broker_outcome(
    *,
    ticket: int,
    symbol: str,
    direction: str,
    deals: list[dict[str, Any]],
    matched_deal: dict[str, Any] | None,
    entry_price: float,
    initial_sl: float,
    final_sl: float,
    tp_price: float,
    volume: float,
    fallback_exit_price: float,
    close_time: datetime,
    entry_time: Any = None,
) -> BrokerOutcome:
    """
    Reconstructs the authoritative broker closure result.

    Deal evidence comes from `mt5.history_deals_get` rows (already flattened
    by the adapter) or from a paper/remote adapter's equivalent dicts. When
    multiple close deals exist for one position they are AGGREGATED (gross
    profit, commission, swap summed; volume summed; never double counted) -
    partial closes produce one outcome row.

    When no deal evidence exists the fallback snapshot estimate is used and
    flagged `reconstruction_source=NONE` so consumers can distinguish a real
    broker result from an estimate.
    """
    deal_rows = [d for d in (deals or []) if d.get("position_ticket") == ticket]
    if matched_deal is not None:
        deal_rows.append(matched_deal)

    if deal_rows:
        gross = 0.0
        comm = 0.0
        swap = 0.0
        deal_ids: list[str] = []
        total_vol = 0.0
        last_price = 0.0
        reason_code = 0
        comment = ""
        for d in deal_rows:
            gross += float(d.get("profit", 0.0) or 0.0)
            comm += float(d.get("commission", 0.0) or 0.0)
            swap += float(d.get("swap", 0.0) or 0.0)
            total_vol += float(d.get("volume", 0.0) or 0.0)
            if d.get("ticket"):
                deal_ids.append(str(d["ticket"]))
            if d.get("price"):
                last_price = float(d["price"])
            if d.get("reason") is not None:
                reason_code = int(d["reason"])
            if d.get("comment"):
                comment = str(d["comment"]) if not comment else f"{comment}; {d['comment']}"
        src = "BROKER_DEALS_AGGREGATED" if len(deal_rows) > 1 else "BROKER_DEALS"
        open_time_str = _iso(entry_time) if entry_time is not None else ""
        duration = 0.0
        if entry_time is not None:
            try:
                duration = max(0.0, (close_time - entry_time).total_seconds())
            except Exception:
                duration = 0.0
        return BrokerOutcome(
            ticket=str(ticket),
            order_id=str(deal_rows[0].get("order_ticket", "") or ""),
            symbol=symbol,
            direction=direction,
            entry_price=float(entry_price or 0.0),
            exit_price=float(last_price or fallback_exit_price or 0.0),
            volume=float(total_vol or volume or 0.0),
            gross_profit=float(gross),
            commission=float(comm),
            swap=float(swap),
            fee=0.0,
            net_pnl_usd=float(gross - abs(comm) - swap),
            open_time=open_time_str,
            close_time=_iso(close_time),
            duration_sec=duration,
            broker_reason_code=reason_code,
            broker_comment=comment,
            deal_ids=deal_ids,
            entry_sl=float(initial_sl or 0.0),
            final_sl=float(final_sl or 0.0),
            entry_tp=float(tp_price or 0.0),
            partial_closes=0,
            reconstruction_source=src,
        )

    # No broker evidence: deterministic snapshot estimate, clearly flagged.
    return BrokerOutcome(
        ticket=str(ticket),
        order_id="",
        symbol=symbol,
        direction=direction,
        entry_price=float(entry_price or 0.0),
        exit_price=float(fallback_exit_price or 0.0),
        volume=float(volume or 0.0),
        gross_profit=0.0,
        commission=0.0,
        swap=0.0,
        fee=0.0,
        net_pnl_usd=0.0,
        open_time=_iso(entry_time) if entry_time is not None else "",
        close_time=_iso(close_time),
        duration_sec=0.0,
        broker_reason_code=0,
        broker_comment="",
        deal_ids=[],
        entry_sl=float(initial_sl or 0.0),
        final_sl=float(final_sl or 0.0),
        entry_tp=float(tp_price or 0.0),
        partial_closes=0,
        reconstruction_source="NONE",
    )


def resolve_outcome_correlation(
    *,
    request_id: str,
    ticket: str,
    ledger: Any,
    build_idempotency_key_fn: Any,
) -> tuple[str, str, str] | None:
    """
    Deterministic experience correlation for a closed ticket.

    Resolution order:
      1. ORIGINAL_REQUEST   - request_id present -> idempotency key derived
         from it (canonical identity, exactly one experience).
      2. POSITION_STATE     - request_id missing but a decision experience
         exists carrying the request as an identifier in the immutable ledger
         (restart / reconciliation recovery; the ledger is the position-state
         authority for correlation).
      3. BROKER_TICKET_FALLBACK - request_id missing AND ledger lookup failed;
         the broker ticket is the ONLY identity -> deterministic
         `exp_bt_<ticket>` key with explicit BROKER_TICKET_FALLBACK provenance.
         The caller MUST verify a matching decision row before recording; if
         none exists the outcome is INVALID with diagnostics, never silent.

    Returns (idempotency_key, correlation_source, correlation_detail) or None
    when the outcome cannot be unambiguously correlated.
    """
    rid = (request_id or "").strip()
    if rid:
        key = build_idempotency_key_fn(rid)
        return key, OutcomeCorrelationSource.ORIGINAL_REQUEST.value, f"request_id={rid}"

    # POSITION_STATE: recover from the immutable ledger by identifier columns.
    if ledger is not None:
        candidates: list[ExperienceRecord] = []
        try:
            candidates = ledger.get_experiences_by_order_id(ticket) or []
        except Exception as e:  # pragma: no cover - defensive
            logger.error("[EXPERIENCE_OUTCOME] ledger correlation lookup failed", error=str(e))
        if len(candidates) == 1:
            rec = candidates[0]
            return (
                rec.idempotency_key,
                OutcomeCorrelationSource.POSITION_STATE.value,
                f"ledger_match ticket={ticket}",
            )
        if len(candidates) > 1:
            # Ambiguous: multiple experiences share the ticket identifier.
            logger.warning(
                "[EXPERIENCE_OUTCOME] correlation AMBIGUOUS",
                ticket=ticket,
                candidates=[c.idempotency_key for c in candidates],
            )
            return None

    # BROKER_TICKET_FALLBACK: deterministic but explicit.
    return (
        build_idempotency_key_fn(f"bt_{ticket}"),
        OutcomeCorrelationSource.BROKER_TICKET_FALLBACK.value,
        f"ticket_fallback ticket={ticket}",
    )


def outcome_row_to_broker_outcome(row: sqlite3.Row | dict[str, Any]) -> BrokerOutcome | None:
    """
    Lifts a persisted outcome row's broker fields into a typed BrokerOutcome
    (used by accounting forensics when the JSON payload predates the typed
    field). Returns None when the row carries no broker evidence.
    """
    if row is None:
        return None
    if isinstance(row, dict):
        get = row.get
    else:
        get = row.__getitem__  # type: ignore[union-attr, assignment]
    try:
        ticket = str(get("execution_id", "") or "")
    except Exception:
        ticket = ""
    return BrokerOutcome(
        ticket=ticket,
        order_id="",
        symbol="",
        direction="",
        entry_price=0.0,
        exit_price=0.0,
        volume=0.0,
        gross_profit=0.0,
        commission=0.0,
        swap=0.0,
        fee=0.0,
        net_pnl_usd=0.0,
        open_time="",
        close_time=str(get("outcome_timestamp", "") or ""),
        duration_sec=0.0,
        broker_reason_code=0,
        broker_comment="",
        deal_ids=[],
        entry_sl=0.0,
        final_sl=0.0,
        entry_tp=0.0,
        partial_closes=0,
        reconstruction_source="NONE",
    )


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        if hasattr(value, "isoformat"):
            dt_value = value
            if getattr(dt_value, "tzinfo", None) is None:
                dt_value = dt_value.replace(tzinfo=UTC)
            return dt_value.isoformat()
    except Exception:
        return str(value)
    return str(value)

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


def classify_exit_with_evidence(
    *,
    deal_reason_code: int,
    comment: str,
    profit_usd: float | None,
    exit_price: float,
    tp_price: float,
    sl_price: float,
    final_sl: float,
    entry_price: float,
    was_sl_modified: bool,
    direction: str,
    forced_mechanism: str | None = None,
) -> tuple[str, str, str, float]:
    """
    Evidence-aware exit classification (TASK-3 / BUG-083/085).

    Returns (exit_reason, evidence_source, evidence_detail, confidence):

      evidence_source  one of ENGINE_FORCED / BROKER_DEAL_REASON /
                       BROKER_DEAL_COMMENT / SL_GEOMETRY / TP_GEOMETRY /
                       STATE_MACHINE / FALLBACK_HEURISTIC
      evidence_detail  machine-readable detail string
      confidence       [0,1] — 1.0 when the broker reason code or comment
                       alone proves the mechanism; lower for geometry/
                       heuristic inference.

    The taxonomy contract (ExitReason) is unchanged; consumers (ledger,
    telegram, accounting) now receive the provenance of the label so an
    exit is never presented as broker-proven when it was inferred.
    """
    if forced_mechanism:
        return (
            forced_mechanism,
            "ENGINE_FORCED",
            f"engine state machine forced exit ({forced_mechanism})",
            1.0,
        )

    reason = int(deal_reason_code or 0)
    comment_l = (comment or "").lower()
    is_buy = "BUY" in str(direction).upper()
    near_sl = sl_price > 0.0 and abs(exit_price - sl_price) < 0.15
    near_tp = tp_price > 0.0 and abs(exit_price - tp_price) < 0.10
    profit = float(profit_usd or 0.0)

    # 1. Broker reason codes (authoritative when present and unambiguous).
    if reason == 5 and (near_tp or "tp" in comment_l):
        return ExitReason.TAKE_PROFIT_HIT, "BROKER_DEAL_REASON", "reason=5 DEAL_REASON_TP", 1.0
    if reason == 4 and (near_sl or "sl" in comment_l):
        return _classify_sl_geometry(
            is_buy=is_buy,
            entry_price=entry_price,
            final_sl=final_sl,
            was_sl_modified=was_sl_modified,
            source="BROKER_DEAL_REASON",
            detail="reason=4 DEAL_REASON_SL",
        )
    if reason == 6 and (near_sl or "sl" in comment_l):
        return _classify_sl_geometry(
            is_buy=is_buy,
            entry_price=entry_price,
            final_sl=final_sl,
            was_sl_modified=was_sl_modified,
            source="BROKER_DEAL_REASON",
            detail="reason=6 DEAL_REASON_SO",
        )
    if reason in (1, 2):
        # Real MT5 client manual close (MOBILE/DESKTOP), no protective evidence.
        return (
            ExitReason.MANUAL_CLOSE,
            "BROKER_DEAL_REASON",
            f"reason={reason} DEAL_REASON_CLIENT",
            1.0,
        )
    if reason == 0:
        # DEAL_REASON_CLIENT is also 0, but a bare 0 with NO comment/geometry/
        # PnL evidence is ambiguous — stay UNKNOWN rather than assume manual
        # (INV-012: UNKNOWN evidence must never be silently promoted).
        # BUG-250 (Agent-12): profit alone must not promote bare reason==0 to MANUAL_CLOSE
        # - comment or price-geometry corroboration required (INV-012).
        if comment_l or near_sl or near_tp:
            return ExitReason.MANUAL_CLOSE, "BROKER_DEAL_REASON", "reason=0 DEAL_REASON_CLIENT", 0.8
        return (
            ExitReason.UNKNOWN,
            "FALLBACK_HEURISTIC",
            "reason=0 with no corroborating evidence",
            0.2,
        )
    if reason == 3:
        # EA/Expert close (NSE engine closes carry this code). Never assume
        # MANUAL (INV-012). The engine's own trailing/BE comments + SL
        # geometry decide the protective class (BUG-081 rule: BE/trailing
        # labels require `was_sl_modified` proof).
        if "sl" in comment_l or "trail" in comment_l:
            return _classify_sl_geometry(
                is_buy=is_buy,
                entry_price=entry_price,
                final_sl=final_sl,
                was_sl_modified=was_sl_modified,
                source="BROKER_DEAL_COMMENT",
                detail=f"reason=3 DEAL_REASON_EXPERT comment={comment}",
            )
        if "tp" in comment_l:
            return (
                ExitReason.TAKE_PROFIT_HIT,
                "BROKER_DEAL_COMMENT",
                "reason=3 DEAL_REASON_EXPERT comment=tp",
                1.0,
            )
        return ExitReason.SYSTEM_CLOSE, "BROKER_DEAL_REASON", "reason=3 DEAL_REASON_EXPERT", 0.9

    # 2. Comment evidence (NSE-generated + broker formats).
    if comment_l.startswith(("nse_close", "nse_trail", "nse_be", "nse_emergency", "nse_cut")):
        return _classify_sl_geometry(
            is_buy=is_buy,
            entry_price=entry_price,
            final_sl=final_sl,
            was_sl_modified=was_sl_modified,
            source="BROKER_DEAL_COMMENT",
            detail=f"comment={comment}",
        )

    # 3. Slope geometry fallback (always a lower-confidence inference).
    if (near_sl or "sl" in comment_l) and final_sl > 0.0 and entry_price > 0.0:
        return _classify_sl_geometry(
            is_buy=is_buy,
            entry_price=entry_price,
            final_sl=final_sl,
            was_sl_modified=was_sl_modified,
            source="SL_GEOMETRY",
            detail="exit near current SL level",
        )
    if near_tp or "tp" in comment_l:
        return ExitReason.TAKE_PROFIT_HIT, "TP_GEOMETRY", "exit near TP level", 0.8

    # 4. PnL-sign heuristic (weakest).
    if profit > 0.0:
        return (
            ExitReason.SYSTEM_CLOSE,
            "FALLBACK_HEURISTIC",
            "positive PnL, no protective evidence",
            0.4,
        )
    if profit < 0.0:
        return (
            ExitReason.SYSTEM_CLOSE,
            "FALLBACK_HEURISTIC",
            "negative PnL, no protective evidence",
            0.4,
        )

    if was_sl_modified and final_sl > 0.0 and entry_price > 0.0:
        if (is_buy and final_sl >= entry_price) or (not is_buy and final_sl <= entry_price):
            return (
                ExitReason.BREAK_EVEN_SL_HIT,
                "SL_GEOMETRY",
                "SL at entry with modification flag",
                0.9,
            )

    return ExitReason.UNKNOWN, "FALLBACK_HEURISTIC", "no evidence", 0.0


def _classify_sl_geometry(
    *,
    is_buy: bool,
    entry_price: float,
    final_sl: float,
    was_sl_modified: bool,
    source: str,
    detail: str,
) -> tuple[str, str, str, float]:
    """
    Classifies an SL-type exit by final-SL geometry vs entry, requiring
    modification proof for break-even/trailing labels (BUG-081 rule).
    """
    if final_sl <= 0.0 or entry_price <= 0.0:
        return ExitReason.HARD_SL_HIT, source, detail, 0.7
    be_tolerance = max(0.5, (entry_price * 0.0005) if entry_price > 0.0 else 0.5)
    within_be = entry_price - be_tolerance <= final_sl <= entry_price + be_tolerance
    if within_be:
        if was_sl_modified:
            return ExitReason.BREAK_EVEN_SL_HIT, source, f"{detail} | SL moved to BE", 1.0
        return ExitReason.HARD_SL_HIT, source, f"{detail} | SL at entry, never modified", 0.9
    if (is_buy and final_sl > entry_price) or (not is_buy and final_sl < entry_price):
        return ExitReason.TRAILING_STOP_HIT, source, f"{detail} | SL trailed beyond entry", 1.0
    return ExitReason.HARD_SL_HIT, source, detail, 0.9


def classify_exit_reason(
    *,
    deal_reason_code: int,
    comment: str,
    profit_usd: float | None,
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

    `profit_usd` may be None (no broker deal matched). When it is None it is
    treated as 0.0 for the *classification heuristic only* — the caller is
    responsible for distinguishing UNKNOWN PnL from genuinely-zero PnL.
    """
    reason, _source, _evidence, _conf = classify_exit_with_evidence(
        deal_reason_code=deal_reason_code,
        comment=comment,
        profit_usd=profit_usd,
        exit_price=exit_price,
        tp_price=tp_price,
        sl_price=sl_price,
        final_sl=final_sl,
        entry_price=entry_price,
        was_sl_modified=was_sl_modified,
        direction=direction,
        forced_mechanism=forced_mechanism,
    )
    return reason


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
        # BUG-084: the caller usually passes `history_deals` which ALREADY
        # contains the matched deal — appending it again double-counted the
        # same physical close (gross profit, volume, deal ids). Dedupe by
        # deal ticket; a duplicate physical deal must never be summed twice.
        matched_ticket = matched_deal.get("ticket")
        if matched_ticket is None or not any(
            str(d.get("ticket", "")) == str(matched_ticket) for d in deal_rows
        ):
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
            net_pnl_usd=float(gross - abs(comm) - abs(swap)),
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

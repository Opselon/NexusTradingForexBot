"""Terminal pending-order outcome emission (P0-A phase 2, BUG-140).

OrderManager-side bridge that closes the experience-lifecycle gap for
decisions whose order never became a trade. Every terminal non-fill path
MUST call :func:`emit_terminal_pending_outcome` exactly once; the underlying
``ExperienceLedger.record_terminal_outcome`` enforces idempotency (an
outcome already present for the idempotency key is never overwritten).

Wired terminal paths (all in OrderManager):
    * ``dispatch_order`` exposure/lot rejection  -> NOT_DISPATCHED
    * ``dispatch_order`` broker rejection        -> EXECUTION_FAILED
      (ticket==0 raised) / REJECTED_UNFILLED (broker accepted then rejected:
      not directly observable at dispatch, recovered by the history sweep)
    * ``manage_pending_orders`` verified cancel  -> CANCELED_UNFILLED
      (AGE_EXPIRATION maps to EXPIRED_UNFILLED)
    * ``refresh_live_tickets_cache`` reconciliation sweep -> CANCELED/EXPIRED/
      REJECTED_UNFILLED from the broker order state (auto-classified)

Nothing here can fabricate a trade: non-trade terminals carry
is_executed=False, is_closed=True, realized R/PnL 0.0, and an explicit
``decision_lifecycle`` marker.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from nexus_scalp.experience.intelligence import ExperienceIntelligenceEngine
from nexus_scalp.experience.lifecycle import (
    LIFECYCLE_PAYLOAD_KEY,
    DecisionLifecycle,
    build_terminal_non_trade_outcome,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.execution.terminal_outcome")

#: Map a dispatch failure reason to the canonical terminal lifecycle state.
def lifecycle_for_dispatch_failure(*, dispatched: bool, broker_rejected: bool) -> DecisionLifecycle:
    if broker_rejected:
        return DecisionLifecycle.REJECTED_UNFILLED
    if dispatched:
        return DecisionLifecycle.EXECUTION_FAILED
    return DecisionLifecycle.NOT_DISPATCHED


def emit_terminal_pending_outcome(
    *,
    experience_engine: Any,
    request_id: str,
    state: DecisionLifecycle,
    detail: str = "",
    outcome_timestamp: datetime | None = None,
    broker_order_id: str = "",
) -> bool:
    """Records the terminal non-trade outcome for one decision.

    Resolves the decision's idempotency key exactly the way the pre-trade
    gate built it (``exp_<request_id>`` via
    ``ExperienceIntelligenceEngine.build_idempotency_key``). Safe to call
    even when the engine is absent or the decision was never persisted:
    the ledger refuses orphan outcomes and the failure is logged.

    Returns True only when a NEW terminal outcome row was queued.
    """
    if experience_engine is None or not request_id:
        return False
    if state not in DecisionLifecycle.TERMINAL_STATES or state in (
        DecisionLifecycle.FILLED_CLOSED,
        DecisionLifecycle.REPAIRED,
    ):
        logger.warning(
            "[TERMINAL_OUTCOME] refused non-terminal or trade state",
            request_id=request_id,
            state=str(state),
        )
        return False
    try:
        key = ExperienceIntelligenceEngine.build_idempotency_key(request_id)
        outcome = build_terminal_non_trade_outcome(
            idempotency_key=key,
            state=state,
            outcome_timestamp=outcome_timestamp or datetime.now(UTC),
            execution_id=broker_order_id,
            detail=detail,
        )
        written = experience_engine.ledger.record_terminal_outcome(outcome)
        if written:
            logger.info(
                "[TERMINAL_OUTCOME] event=RECORDED",
                request_id=request_id,
                idempotency_key=key,
                state=state.value,
                detail=detail[:120],
            )
        return bool(written)
    except Exception as e:
        # Learning is non-critical: never disturb the execution path.
        logger.error(
            "[TERMINAL_OUTCOME] emission failed (isolated)",
            request_id=request_id,
            state=state.value,
            error=str(e),
        )
        return False


__all__ = [
    "emit_terminal_pending_outcome",
    "lifecycle_for_dispatch_failure",
    "LIFECYCLE_PAYLOAD_KEY",
]

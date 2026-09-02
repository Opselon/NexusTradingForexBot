"""Canonical Decision Lifecycle (P0-A, BUG-140 remediation).

One authoritative, importable model of a research/experience DECISION's
execution lifecycle. Consumers:

    * execution  (OrderManager)          -> reports terminal pending states
    * experience (ledger / intelligence) -> persists terminal outcomes
    * research   (dataset.py)            -> classifies eligibility
    * web        (diagnostics/summary)   -> truthful terminal-state counters

The lifecycle EXTENDS the existing Phase 08/14 model (ExperienceRecord +
ExperienceOutcome rows, `is_executed` / `is_closed` / `exit_reason`); it does
NOT replace it. Terminal states are recorded as outcome rows so the
"decision exists AND outcome missing" gap can never persist indefinitely.

Invariant: for every `audit_experiences` row there is eventually exactly one
`audit_experience_outcomes` row (UNIQUE idempotency_key, ON CONFLICT DO
NOTHING already enforces idempotency at the storage layer). A missing outcome
means "not yet terminal", never a legitimate end state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from nexus_scalp.experience.models import ExperienceOutcome


class DecisionLifecycle(StrEnum):
    """Terminal and transient execution states of one experience decision.

    Transient states are only ever reported (the DB stores decisions plus one
    outcome event); terminal states MUST be persisted as an outcome row.

    Trade-producing terminals (realized R is meaningful):
        FILLED_CLOSED               fill -> position -> closed with broker result
    Non-trade terminals (NO realized R: never fabricate one):
        CANCELED_UNFILLED           pending canceled by us before any fill
        EXPIRED_UNFILLED            pending expired at the broker (TTL/GTC)
        REJECTED_UNFILLED           broker rejected the order before any fill
        REPLACED_UNFILLED           pending replaced/superseded before any fill
        EXECUTION_FAILED            dispatch failed (no broker ticket ever existed)
        NOT_DISPATCHED              decision never sent (gate/exposure/shutdown)

    Fill known but result lost (degraded evidence, recovery queue):
        FILLED_OUTCOME_MISSING      broker fill evidence exists, no close result
    """

    DECISION_CREATED = "DECISION_CREATED"
    DISPATCH_PENDING = "DISPATCH_PENDING"
    DISPATCHED = "DISPATCHED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    POSITION_OPEN = "POSITION_OPEN"
    POSITION_CLOSED = "POSITION_CLOSED"

    CANCELED_UNFILLED = "CANCELED_UNFILLED"
    EXPIRED_UNFILLED = "EXPIRED_UNFILLED"
    REJECTED_UNFILLED = "REJECTED_UNFILLED"
    REPLACED_UNFILLED = "REPLACED_UNFILLED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    NOT_DISPATCHED = "NOT_DISPATCHED"
    FILLED_OUTCOME_MISSING = "FILLED_OUTCOME_MISSING"

    FILLED_CLOSED = "FILLED_CLOSED"
    REPAIRED = "REPAIRED"


#: States that carry a realized (executed + closed) trade result.
TRADE_TERMINAL_STATES: frozenset[DecisionLifecycle] = frozenset(
    {DecisionLifecycle.FILLED_CLOSED, DecisionLifecycle.REPAIRED}
)

#: Terminal states where the decision never became a trade (no realized R).
NON_TRADE_TERMINAL_STATES: frozenset[DecisionLifecycle] = frozenset(
    {
        DecisionLifecycle.CANCELED_UNFILLED,
        DecisionLifecycle.EXPIRED_UNFILLED,
        DecisionLifecycle.REJECTED_UNFILLED,
        DecisionLifecycle.REPLACED_UNFILLED,
        DecisionLifecycle.EXECUTION_FAILED,
        DecisionLifecycle.NOT_DISPATCHED,
    }
)

#: Degraded-evidence terminals: a fill happened but the result is missing.
DEGRADED_TERMINAL_STATES: frozenset[DecisionLifecycle] = frozenset(
    {DecisionLifecycle.FILLED_OUTCOME_MISSING}
)

TERMINAL_STATES: frozenset[DecisionLifecycle] = (
    TRADE_TERMINAL_STATES | NON_TRADE_TERMINAL_STATES | DEGRADED_TERMINAL_STATES
)

#: Structured payload key stamped on every terminal non-trade outcome row so
#: the classification survives persistence and is readable by any consumer.
LIFECYCLE_PAYLOAD_KEY: str = "decision_lifecycle"

#: Marker stamped by the historical recovery job on reconstructed outcomes.
RECOVERY_SOURCE_BROKER_HISTORY: str = "broker_history_recovery"

#: exit_reason values used for terminal non-trade outcomes. Distinct from the
#: canonical broker-close ExitReason taxonomy (those describe POSITIVE closes);
#: these describe why a decision never became (or lost) a realized trade.
TERMINAL_NON_TRADE_EXIT_REASONS: dict[DecisionLifecycle, str] = {
    DecisionLifecycle.CANCELED_UNFILLED: "CANCELED_UNFILLED",
    DecisionLifecycle.EXPIRED_UNFILLED: "EXPIRED_UNFILLED",
    DecisionLifecycle.REJECTED_UNFILLED: "REJECTED_UNFILLED",
    DecisionLifecycle.REPLACED_UNFILLED: "REPLACED_UNFILLED",
    DecisionLifecycle.EXECUTION_FAILED: "EXECUTION_FAILED",
    DecisionLifecycle.NOT_DISPATCHED: "NOT_DISPATCHED",
    DecisionLifecycle.FILLED_OUTCOME_MISSING: "FILLED_OUTCOME_MISSING",
}

#: Reverse map: persisted exit_reason -> lifecycle state (deterministic
#: classification of historical + incoming outcome rows).
_EXIT_REASON_TO_LIFECYCLE: dict[str, DecisionLifecycle] = {
    reason: state for state, reason in TERMINAL_NON_TRADE_EXIT_REASONS.items()
}


def lifecycle_from_outcome(
    *,
    is_executed: bool,
    is_closed: bool,
    exit_reason: str,
    decision_lifecycle: str = "",
) -> DecisionLifecycle:
    """Deterministically classifies a merged decision+outcome row.

    Preference order:
      1. explicit `decision_lifecycle` payload marker (new rows)
      2. terminal non-trade exit_reason markers
      3. executed+closed  -> FILLED_CLOSED (legacy rows carry a broker close)
      4. otherwise        -> DECISION_CREATED (outcome not yet terminal)
    """
    if decision_lifecycle:
        try:
            state = DecisionLifecycle(decision_lifecycle)
            if state in TERMINAL_STATES:
                return state
        except ValueError:
            pass
    marker = _EXIT_REASON_TO_LIFECYCLE.get(str(exit_reason or "").upper())
    if marker is not None:
        return marker
    if is_executed and is_closed:
        return DecisionLifecycle.FILLED_CLOSED
    if is_closed:
        # closed-but-never-executed legacy shape (no explicit marker)
        return DecisionLifecycle.NOT_DISPATCHED
    return DecisionLifecycle.DECISION_CREATED


def build_terminal_non_trade_outcome(
    *,
    idempotency_key: str,
    state: DecisionLifecycle,
    outcome_timestamp: datetime | None = None,
    execution_id: str = "",
    detail: str = "",
) -> ExperienceOutcome:
    """Builds the append-only terminal outcome for a non-trade decision.

    Realized PnL/R stay 0.0 (real truth: no trade happened) and the row is
    classified is_executed=False / is_closed=True with the exact lifecycle
    state stamped in `exit_reason` AND the typed `decision_lifecycle` field,
    so dataset classification can separate "never traded" from "result lost"
    without inventing a fake R.
    """
    if state not in NON_TRADE_TERMINAL_STATES and state not in DEGRADED_TERMINAL_STATES:
        raise ValueError(f"not a non-trade terminal state: {state}")
    reason = TERMINAL_NON_TRADE_EXIT_REASONS[state]
    return ExperienceOutcome(
        idempotency_key=idempotency_key,
        execution_id=execution_id,
        outcome_timestamp=outcome_timestamp or datetime.now(UTC),
        is_executed=False,
        is_closed=True,
        exit_reason=reason,
        realized_pnl_usd=0.0,
        realized_r_multiple=0.0,
        approved_volume=0.0,
        decision_lifecycle=state.value,
        lifecycle_detail=detail,
    )

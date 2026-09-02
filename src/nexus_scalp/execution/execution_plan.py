"""ExecutionPlan — frozen intent representation for position actions.

S6-dispatch seam (Agent-5, CHG-0032/TASK-OM-P0-DECOMP): the manager's
decision stages produce an action; the plan captures that intent explicitly
before the broker dispatcher runs. The plan represents INTENT ONLY:

- no broker access, no risk authority, no policy logic, no side effects
- deterministic frozen dataclass (hashable, cheap to construct)
- built from values the decision stage already computed

The broker dispatcher (OrderLifecycleManager._execute_position_action)
consumes an approved plan and executes it with exact historical call
ordering; it never originates actions of its own.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionPlan:
    """Immutable per-tick action intent for one ticket."""

    action: str  # CLOSE | MODIFY_SL | PARTIAL_CLOSE | BREAK_EVEN | NORMAL_TRAIL
    scenario: str  # decision-stage scenario label
    ticket: int  # broker ticket
    symbol: str  # broker symbol
    rule_target_sl: float = 0.0  # rule-matrix / giveback SL target (0.0 = none)
    mechanism: object = None  # ExitMechanism already tagged (or None)

    def __post_init__(self) -> None:
        if not self.action:
            raise ValueError("ExecutionPlan.action must be non-empty")
        if self.ticket <= 0:
            raise ValueError("ExecutionPlan.ticket must be a positive broker ticket")

"""
Strategy Research Lifecycle State Machine
=========================================
PHASE 09B (spec 19 / 30).

Required conceptual states:
    DISCOVERED -> BACKTESTING -> VALIDATING -> OOS_TESTING -> ROBUSTNESS_TESTING
        -> VALIDATED -> SHADOW -> ACTIVE
Failure paths:
    REJECTED, DEGRADED, RETIRED

A strategy MUST NOT skip validation, and CANNOT reach ACTIVE without passing
all gates and an explicit operator approval (promotion is NOT automatic).
"""

from __future__ import annotations

from nexus_scalp.research.models import CandidateLifecycle

#: State machine adjacency.
_TRANSITIONS: dict[CandidateLifecycle, set[CandidateLifecycle]] = {
    CandidateLifecycle.DISCOVERED: {CandidateLifecycle.BACKTESTING, CandidateLifecycle.REJECTED},
    CandidateLifecycle.BACKTESTING: {CandidateLifecycle.VALIDATING, CandidateLifecycle.REJECTED},
    CandidateLifecycle.VALIDATING: {CandidateLifecycle.OOS_TESTING, CandidateLifecycle.REJECTED},
    CandidateLifecycle.OOS_TESTING: {
        CandidateLifecycle.ROBUSTNESS_TESTING,
        CandidateLifecycle.REJECTED,
    },
    CandidateLifecycle.ROBUSTNESS_TESTING: {
        CandidateLifecycle.VALIDATED,
        CandidateLifecycle.REJECTED,
        CandidateLifecycle.DEGRADED,
    },
    CandidateLifecycle.VALIDATED: {
        CandidateLifecycle.SHADOW,
        CandidateLifecycle.REJECTED,
        CandidateLifecycle.DEGRADED,
    },
    CandidateLifecycle.SHADOW: {
        CandidateLifecycle.ACTIVE,
        CandidateLifecycle.DEGRADED,
        CandidateLifecycle.REJECTED,
    },
    CandidateLifecycle.ACTIVE: {CandidateLifecycle.DEGRADED, CandidateLifecycle.RETIRED},
    CandidateLifecycle.DEGRADED: {
        CandidateLifecycle.RETIRED,
        CandidateLifecycle.REJECTED,
        CandidateLifecycle.VALIDATED,
    },
    CandidateLifecycle.REJECTED: set(),
    CandidateLifecycle.RETIRED: set(),
}


class LifecycleError(ValueError):
    """Raised on an illegal lifecycle transition (e.g. skipping validation)."""


def can_transition(current: CandidateLifecycle, target: CandidateLifecycle) -> bool:
    return current in _TRANSITIONS and target in _TRANSITIONS[current]


def transition(current: CandidateLifecycle, target: CandidateLifecycle) -> CandidateLifecycle:
    """
    Returns the new lifecycle, enforcing the state machine.

    Raises LifecycleError if the target is not a legal next state.
    """
    if not can_transition(current, target):
        raise LifecycleError(f"Illegal lifecycle transition: {current.value} -> {target.value}")
    return target


def approve_for_live(candidate: CandidateLifecycle) -> CandidateLifecycle:
    """
    Deliberate, operator-gated promotion to ACTIVE (spec 21).

    ONLY a SHADOW (or previously-validated) candidate may become ACTIVE, and
    never automatically (no Candidate -> Auto Live).
    """
    if candidate not in (CandidateLifecycle.SHADOW, CandidateLifecycle.VALIDATED):
        raise LifecycleError(
            f"Cannot promote {candidate.value} to ACTIVE: must be SHADOW/VALIDATED first"
        )
    return CandidateLifecycle.ACTIVE


def require_validation_gate(lifecycle: CandidateLifecycle) -> None:
    """
    A strategy may not be trade-eligible unless it reached VALIDATED or beyond.
    REJECTED / RETIRED / DEGRADED / DISCOVERED / BACKTESTING etc. are ineligible.
    """
    eligible = {CandidateLifecycle.VALIDATED, CandidateLifecycle.SHADOW, CandidateLifecycle.ACTIVE}
    if lifecycle not in eligible:
        raise LifecycleError(
            f"Strategy lifecycle {lifecycle.value} is not validation-gated for live use"
        )

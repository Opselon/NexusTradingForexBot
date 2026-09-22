"""Layer-2 Position Decision Adviser — runtime state and activation contract.

TASK-POSA-001. This is an OPTIONAL, opt-in advisory model that sits alongside
the existing hold/close decide system. It is OFF by default and can never
remove, replace, or relax any existing execution, risk, or protection logic.

Authority model (invariant, enforced in PositionAdviserService):
    * The adviser can only ever *recommend*. It emits a PositionAdvisory with
      a confidence and an action in {KEEP, CLOSE, REDUCE}.
    * The decide system may apply a BOUNDED hold-score adjustment when the
      operator has enabled it. The adviser can only ever LOWER the hold score
      (a negative adjustment) — never raise it, never extend a position's life,
      never override a protection verdict, and never open or size a position.
    * When disabled (the default) the service returns ``None`` from
      ``evaluate()`` and the decide system runs byte-identically to today.

Activation ladder (the UI drives this; each step must be verified by real
broker/position checks before the next one becomes selectable):
    DISABLED  -> the adviser is loaded but never consulted.
    PAPER     -> advisory only, logged, no hold-score influence at all.
    LIVE      -> advisory + bounded hold-score adjustment. Requires the broker
                 connectivity + live position checks to have passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AdviserActivation(StrEnum):
    """Activation states. DISABLED is the default and the safe floor."""

    DISABLED = "DISABLED"
    PAPER = "PAPER"
    LIVE = "LIVE"


#: The three discrete actions the Layer-2 position model is trained to predict.
#: Mirrors ``position_replay.PositionStateObservation.optimal_action``.
ADVISER_ACTIONS: tuple[str, ...] = ("KEEP", "CLOSE", "REDUCE")

#: Map from action index (model class order, TRAINED_CLASS_COUNT=3) to name.
ACTION_BY_INDEX: dict[int, str] = dict(enumerate(ADVISER_ACTIONS))

#: Inverse map for the trainer.
INDEX_BY_ACTION: dict[str, int] = {name: idx for idx, name in enumerate(ADVISER_ACTIONS)}

#: Minimum model output width. The adviser head is exactly
#: TRAINED_CLASS_COUNT (3) — see model_class_contract. Anything narrower is a
#: load-time refusal; a wider legacy head is never silently truncated.
MIN_ADVISER_CLASSES = 3


@dataclass(frozen=True)
class PositionAdvisory:
    """One adviser verdict for one open position.

    ``hold_score_adjustment`` is the ONLY channel through which the adviser can
    touch execution. It is <= 0 by construction (see _bounded_adjustment) and is
    applied on top of the existing hold-score evaluation, never in place of it.
    """

    ticket: int
    action: str
    confidence: float
    probabilities: dict[str, float]
    hold_score_adjustment: float
    activation: AdviserActivation
    model_id: str
    model_dimension: int
    evaluated_at: str
    latency_ms: float
    advisory_id: str
    #: True only when this verdict influenced the hold score (LIVE + enabled).
    applied: bool = False
    #: Reason the verdict did NOT apply (disabled / paper / error / refusals).
    not_applied_reason: str = ""
    #: Structured cause for diagnostics; never used for control flow.
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket": self.ticket,
            "action": self.action,
            "confidence": self.confidence,
            "probabilities": dict(self.probabilities),
            "hold_score_adjustment": self.hold_score_adjustment,
            "activation": str(self.activation),
            "model_id": self.model_id,
            "model_dimension": self.model_dimension,
            "evaluated_at": self.evaluated_at,
            "latency_ms": round(self.latency_ms, 3),
            "advisory_id": self.advisory_id,
            "applied": self.applied,
            "not_applied_reason": self.not_applied_reason,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class ActivationCheckResult:
    """Outcome of one activation-ladder prerequisite check.

    Checks are real probes (broker connectivity, live position enumeration,
    spread/permission gates) — never self-asserted. A LIVE activation is
    refused unless every required check reports ``passed=True`` with evidence.
    """

    name: str
    passed: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": bool(self.passed),
            "detail": self.detail,
            "evidence": dict(self.evidence),
        }


__all__ = [
    "ACTION_BY_INDEX",
    "ADVISER_ACTIONS",
    "INDEX_BY_ACTION",
    "MIN_ADVISER_CLASSES",
    "ActivationCheckResult",
    "AdviserActivation",
    "PositionAdvisory",
]

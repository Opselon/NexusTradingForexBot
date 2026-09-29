"""ML Position Controller — structured decision object (spec §3).

The model is the WHOLE position controller: KEEP/CLOSE as the primary
decision with independent management dimensions layered on top. KEEP +
MODIFY_SL + MODIFY_TP is legal; CLOSE + FAST_CLOSE is legal. This is NOT a
single mutually-exclusive enum.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.position_adviser.ownership import OwnershipViolation

__all__ = ["CloseMode", "MLPositionDecision", "PositionAction"]


class PositionAction:
    """Primary position disposition (independent from management dims)."""

    KEEP = "KEEP"
    CLOSE = "CLOSE"


class CloseMode:
    """How a CLOSE is executed (layered on PositionAction)."""

    NONE = "NONE"
    NORMAL = "NORMAL"
    FAST = "FAST"


class SlTpAction:
    """Per-dimension management action."""

    NONE = "NONE"
    MODIFY = "MODIFY"


@dataclass(frozen=True)
class MLPositionDecision:
    """One ML controller verdict for one open position.

    ``risk_state`` carries model-internal decision-state signals
    (calm/stress/fear/greed) — features of the decision, NOT claims about
    human emotion. ``confidence`` gates actuation (caller policy).
    """

    ticket: int
    position_action: str  # KEEP | CLOSE
    close_mode: str = CloseMode.NONE  # NONE | NORMAL | FAST
    sl_action: str = SlTpAction.NONE  # NONE | MODIFY
    tp_action: str = SlTpAction.NONE
    new_sl: float = 0.0
    new_tp: float = 0.0
    risk_state: dict[str, float] = field(
        default_factory=lambda: {"calm": 0.0, "stress": 0.0, "fear": 0.0, "greed": 0.0}
    )
    confidence: float = 0.0
    model_version: str = ""
    feature_schema_version: str = ""
    decision_timestamp: str = ""
    #: Broker mutation intents the execution gate will authorize (derived,
    #: not free-form): each entry is (action, params) the gate must allow
    #: for the ML actor.
    mutations: tuple[tuple[str, dict[str, Any]], ...] = ()

    def __post_init__(self) -> None:
        if self.position_action not in (PositionAction.KEEP, PositionAction.CLOSE):
            raise ValueError(f"position_action must be KEEP|CLOSE, got {self.position_action!r}")
        if self.position_action == PositionAction.KEEP and self.close_mode != CloseMode.NONE:
            raise ValueError("close_mode must be NONE when position_action is KEEP")
        if self.sl_action == SlTpAction.MODIFY and self.new_sl <= 0.0:
            raise ValueError("sl_action=MODIFY requires a positive new_sl")
        if self.tp_action == SlTpAction.MODIFY and self.new_tp <= 0.0:
            raise ValueError("tp_action=MODIFY requires a positive new_tp")
        if self.sl_action not in (SlTpAction.NONE, SlTpAction.MODIFY):
            raise ValueError(f"sl_action must be NONE|MODIFY, got {self.sl_action!r}")
        if self.tp_action not in (SlTpAction.NONE, SlTpAction.MODIFY):
            raise ValueError(f"tp_action must be NONE|MODIFY, got {self.tp_action!r}")

    # ---------------------------------------------------------- derived

    @property
    def is_close(self) -> bool:
        return self.position_action == PositionAction.CLOSE

    @property
    def is_fast_close(self) -> bool:
        return self.is_close and self.close_mode == CloseMode.FAST

    @property
    def modifies_sl(self) -> bool:
        return self.sl_action == SlTpAction.MODIFY

    @property
    def modifies_tp(self) -> bool:
        return self.tp_action == SlTpAction.MODIFY

    def broker_mutations(self) -> tuple[tuple[str, dict[str, Any]], ...]:
        """The (action, params) sequence to execute against the broker."""
        out: list[tuple[str, dict[str, Any]]] = []
        if self.is_close:
            out.append(
                (
                    "FAST_CLOSE" if self.is_fast_close else "CLOSE",
                    {"ticket": self.ticket},
                )
            )
        if self.modifies_sl or self.modifies_tp:
            out.append(
                (
                    "MODIFY_SL_TP",
                    {
                        "ticket": self.ticket,
                        "stop_loss": self.new_sl if self.modifies_sl else None,
                        "take_profit": self.new_tp if self.modifies_tp else None,
                    },
                )
            )
        return tuple(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket": self.ticket,
            "position_action": self.position_action,
            "close_mode": self.close_mode,
            "sl_action": self.sl_action,
            "tp_action": self.tp_action,
            "new_sl": self.new_sl,
            "new_tp": self.new_tp,
            "risk_state": dict(self.risk_state),
            "confidence": self.confidence,
            "model_version": self.model_version,
            "feature_schema_version": self.feature_schema_version,
            "decision_timestamp": self.decision_timestamp,
            "mutations": [{"action": a, "params": dict(p)} for a, p in self.broker_mutations()],
        }


def enforce_decision_gate(
    decision: MLPositionDecision, gate: Any
) -> list[tuple[str, dict[str, Any]]]:
    """Run every broker mutation in ``decision`` through the ownership gate.

    Returns the authorized list. Raises :class:`OwnershipViolation` on the
    first blocked mutation — the ML controller never partially executes.
    """
    authorized: list[tuple[str, dict[str, Any]]] = []
    for action, params in decision.broker_mutations():
        gate.authorize_or_raise(ticket=decision.ticket, action=action, actor="ml")
        authorized.append((action, params))
    return authorized


_ = OwnershipViolation  # re-exported for callers' convenience

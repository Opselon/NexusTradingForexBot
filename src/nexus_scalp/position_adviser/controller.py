"""ML Position Controller — execution engine (spec §3/§18).

Runs an :class:`MLPositionDecision` through the ownership gate and the RAW
broker adapter (the controller IS the owner; it does not pass through the
legacy-gated wrapper). Every mutation is authorized first; a blocked
mutation aborts the whole decision (no partial execution).
"""

from __future__ import annotations

from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.position_adviser.decision import MLPositionDecision, enforce_decision_gate
from nexus_scalp.position_adviser.ownership import OwnershipViolation

logger = get_logger("nexus_scalp.position_adviser.controller")

__all__ = ["MLPositionController", "ControllerExecutionError"]


class ControllerExecutionError(RuntimeError):
    """A decision failed at the execution layer (after authorization)."""


class MLPositionController:
    """Executes ML decisions against the broker, ownership-enforced."""

    def __init__(self, *, raw_adapter: Any, gate: Any, model_version: str, schema_version: str) -> None:
        self._adapter = raw_adapter
        self._gate = gate
        self.model_version = model_version
        self.schema_version = schema_version

    def execute(self, decision: MLPositionDecision) -> dict[str, Any]:
        """Authorize + execute every mutation in the decision. Atomic-ish:
        authorization completes for ALL mutations before ANY executes."""
        authorized = enforce_decision_gate(decision, self._gate)

        results: list[dict[str, Any]] = []
        try:
            for action, params in authorized:
                if action in ("CLOSE", "FAST_CLOSE"):
                    ok = bool(self._adapter.close_position(ticket=decision.ticket))
                elif action == "MODIFY_SL_TP":
                    ok = bool(
                        self._adapter.modify_position(
                            ticket=decision.ticket,
                            stop_loss=float(params.get("stop_loss") or 0.0),
                            take_profit=float(params.get("take_profit") or 0.0),
                        )
                    )
                else:  # pragma: no cover - decision.broker_mutations is closed
                    raise ControllerExecutionError(f"unknown mutation {action!r}")
                results.append({"action": action, "ok": ok})
                if not ok:
                    raise ControllerExecutionError(
                        f"broker refused {action} for ticket {decision.ticket}"
                    )
        except OwnershipViolation:
            raise
        except Exception as exc:
            # Surface controller failure WITHOUT giving legacy control (§17).
            self._gate.set_ml_health(False, str(exc))
            raise ControllerExecutionError(str(exc)) from exc

        logger.info(
            "[ML_CTRL] event=DECISION_EXECUTED ticket=%s action=%s mutations=%d",
            decision.ticket,
            decision.position_action,
            len(results),
        )
        return {"status": "OK", "executed": results, "decision": decision.to_dict()}

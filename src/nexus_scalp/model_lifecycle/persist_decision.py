"""
Candidate Persist-Decision API (BUG-236 / MLFix F7 persist-path guard)
======================================================================

TASK-ID: MLFIX-T3. Single authoritative point that decides whether a trained
online fine-tune candidate may be PERSISTED as a new model artifact.

Invariant (enforced here, consumed by ``WalkForwardTrainer.fine_tune_online``):

    candidate rejected  OR  zero improvement  OR  quality gate failed  OR
    health gate failed
        => ``persist=False`` with a recorded machine-readable reason, and NO
        artifact write of any kind (no model save, no champion update, no
        registry row claiming replacement).

The decision is EXPLICIT and auditable: every reason code is a stable string
constant, ``PersistDecision`` is frozen, and the trainer attaches the decision
to the returned model (``_finetune_decision``) so the LiveEngine hot path can
skip the atomic save + provenance re-registration without duplicating gate
logic (BUG-235: the engine previously re-persisted rejected baselines
unconditionally — "ASYNC RETRAIN SUCCESS" after accepted=False).

Design notes:
- ``walk_forward_trainer`` may NOT import torch-heavy lifecycle modules at
  module scope (import cycles / weight), so this module is dependency-light:
  stdlib + numpy + torch only.
- ``should_persist_candidate`` is pure: it NEVER touches the filesystem and
  NEVER mutates the model. Callers act on the verdict.
- The trainer-level early exits (insufficient rows, insufficient labels)
  produce ``NOT_TRAINED_*`` verdicts: nothing was learned, so nothing may be
  written as a "new" model. (The cold-start fallback scaler save is a separate,
  documented resilience behavior — it never claims a model replacement.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Machine-readable reason codes (stable contract — log/audit consumers rely
# on the exact strings; do not rename, only append).
# ---------------------------------------------------------------------------
REASON_ACCEPTED = "ACCEPTED_QUALITY_GATE_PASSED"
REASON_ZERO_IMPROVEMENT = "ZERO_IMPROVEMENT_BASELINE_KEPT"
REASON_QUALITY_GATE_FAILED = "QUALITY_GATE_FAILED"
REASON_HEALTH_GATE_FAILED = "HEALTH_GATE_FAILED"
REASON_NOT_TRAINED_INSUFFICIENT_ROWS = "NOT_TRAINED_INSUFFICIENT_ROWS"
REASON_NOT_TRAINED_INSUFFICIENT_LABELS = "NOT_TRAINED_INSUFFICIENT_LABELS"


@dataclass(frozen=True)
class PersistDecision:
    """Immutable verdict for one trained candidate.

    Attributes:
        persist: True only when the candidate may replace/persist as a new
            model artifact. All rejection paths set False.
        reason: machine-readable code (see module constants).
        detail: human-readable context (rejection reasons, metrics snapshot).
    """

    persist: bool
    reason: str
    detail: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.persist

    def model_dump(self) -> dict[str, Any]:
        """JSON-safe dump for meta/audit payloads."""
        return {
            "persist": bool(self.persist),
            "reason": self.reason,
            "detail": self.detail,
            "metrics": dict(self.metrics),
        }


def should_persist_candidate(
    *,
    trained: bool = True,
    zero_improvement: bool = False,
    quality_gate_passed: bool = True,
    health_ok: bool = True,
    accepted: bool = False,
    insufficient_rows: bool = False,
    insufficient_labels: bool = False,
    rejection_reasons: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
) -> PersistDecision:
    """Decide whether a trained candidate may be persisted as a new model.

    The invariant this function guarantees (single source of truth):

        rejected OR zero improvement OR quality gate failed OR health gate
        failed OR never trained => persist=False with a recorded reason.

    Args:
        trained: False when the run never reached a training outcome
            (e.g. buffer too small). ``insufficient_rows`` /
            ``insufficient_labels`` refine the reason when provided.
        zero_improvement: BUG-228 honest-skip condition — early stopping
            restored the baseline unchanged; there is nothing new to persist.
        quality_gate_passed: the trainer's multi-metric quality gate verdict.
        health_ok: behavioral/health gate verdict (kept False when a health
            probe rejects the candidate).
        accepted: the trainer's composite accept flag. When False without any
            more specific rejection flag this maps to
            ``REASON_QUALITY_GATE_FAILED`` (fail-closed: an unexplained
            non-acceptance can never persist).
        rejection_reasons: human-readable gate rejection strings for audit.
        metrics: optional metrics snapshot carried on the decision.

    Returns:
        PersistDecision (frozen). ``persist=True`` ONLY for the fully
        accepted path.
    """
    reasons = list(rejection_reasons or [])
    snap = dict(metrics or {})

    if insufficient_rows:
        return PersistDecision(
            persist=False,
            reason=REASON_NOT_TRAINED_INSUFFICIENT_ROWS,
            detail="buffer too small after tail purge; nothing was trained",
            metrics=snap,
        )
    if insufficient_labels:
        return PersistDecision(
            persist=False,
            reason=REASON_NOT_TRAINED_INSUFFICIENT_LABELS,
            detail="post-purge labeled rows below training minimum",
            metrics=snap,
        )
    if not trained:
        return PersistDecision(
            persist=False,
            reason=REASON_NOT_TRAINED_INSUFFICIENT_ROWS,
            detail="run produced no training outcome",
            metrics=snap,
        )
    if zero_improvement:
        return PersistDecision(
            persist=False,
            reason=REASON_ZERO_IMPROVEMENT,
            detail="early stopping restored the baseline unchanged; keeping baseline weights",
            metrics=snap,
        )
    if not health_ok:
        return PersistDecision(
            persist=False,
            reason=REASON_HEALTH_GATE_FAILED,
            detail="; ".join(reasons) or "behavioral/health gate rejected the candidate",
            metrics=snap,
        )
    if not quality_gate_passed or not accepted:
        # Fail-closed: any non-acceptance that reaches here (including an
        # unexplained accepted=False) blocks persistence.
        return PersistDecision(
            persist=False,
            reason=REASON_QUALITY_GATE_FAILED,
            detail="; ".join(reasons) or "candidate not accepted by the quality gate",
            metrics=snap,
        )
    return PersistDecision(
        persist=True,
        reason=REASON_ACCEPTED,
        detail="candidate accepted; persistence authorized",
        metrics=snap,
    )


def attach_decision(model: Any, decision: PersistDecision) -> Any:
    """Attach a PersistDecision to a returned model (trainer -> engine signal).

    The LiveEngine hot path reads ``_finetune_decision`` (falling back to the
    legacy ``_finetune_accepted`` flags) to skip the atomic save + provenance
    re-registration when ``persist`` is False. Attachment never raises: a
    frozen/odd model object must not turn a training result into a crash.
    """
    try:
        model._finetune_decision = decision  # type: ignore[attr-defined]
        model._finetune_accepted = bool(decision.persist)  # type: ignore[attr-defined]
        model._finetune_zero_improvement = (  # type: ignore[attr-defined]
            decision.reason == REASON_ZERO_IMPROVEMENT
        )
    except Exception:  # pragma: no cover - defensive
        pass
    return model


def decision_of(model: Any) -> PersistDecision | None:
    """Read the PersistDecision attached to a model, if any."""
    decision = getattr(model, "_finetune_decision", None)
    return decision if isinstance(decision, PersistDecision) else None


def should_persist_model(model: Any) -> bool:
    """Engine-side convenience: may this returned model be persisted?

    True only when a PersistDecision is attached AND it authorizes
    persistence. No decision attached => False (fail-closed): an untagged
    model must never be persisted by the BUG-235 guard.
    """
    decision = decision_of(model)
    return bool(decision is not None and decision.persist)

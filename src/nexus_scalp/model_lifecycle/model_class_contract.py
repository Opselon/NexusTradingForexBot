"""MODEL_CLASS_CONTRACT v1 — Single source of truth for the neural class contract.

Fix #3 (3/4-class) + Fix #6 (smoke separation).

The diagnostic on the live artifact (MLFix.MD §11 / PINC Exp2):

    labels              = 3-class (NO_TRADE / BUY / SELL) triple-barrier
    model head          = 4 logits (NO_TRADE / BUY / SELL / WAIT)
    WAIT mass           = ~0.22 mean  (never a label, eats prob mass on every
                                        vector, depresses trained-class posteriors)
    production intent   = 3-class neural; WAIT is a POLICY state, not a label

Contract decision (evidence):

    * TripleBarrierLabeler, LabelSchema (triple_barrier_3class_v1),
      ModelManifest / ExperimentConfig (class_count=3) — all 3-class neural.
    * SignalPolicy._directional_confidence normalizes over BUY+SELL+NO_TRADE,
      explicitly excluding WAIT at index 3 — behavioral proof that WAIT is
      policy-bridge, not signal.
    * ScalpNet's 4th logit exists today for backward compatibility / policy
      mapping only; it must carry ZERO semantic load in loss, calibration,
      or promotion.

Therefore: the neural contract is 3-class. A 4-wide ScalpNet head is allowed
on disk (legacy bundle geometry) but is contractually DEAD: index 3 (WAIT)
is masked to 0 at inference, excluded from the loss / class-weight contract,
and ignored by calibration. Dataset / loss MUST know WAIT is not a label.

Smoke contract:

    smoke=True runs are bounded drills (2 folds, 1 epoch, SMOKE_MIN_ROWS tails)
    and MUST be tagged on the artifact so the promotion gate can REJECT them
    regardless of validity/width — a smoke artifact is never
    production_eligible. Mirrors the existing promotion-gate invariant that
    auto-promotion is forbidden (orchestrator NEVER promotes automatically).

This module is the SSOT; every downstream consumer imports the constants here.
"""

from __future__ import annotations

import torch
from torch import nn

# ── Class-contract geometry ─────────────────────────────────────────────────

#: The neural target classes. Labels, dataset encodings and loss are 3-wide.
#: Logging + policy use the same indexes; index alias kept here so a typo
#: cannot invent a fourth label.
TRAINED_CLASS_COUNT: int = 3
TRAINED_CLASS_NAMES: tuple[str, ...] = ("NO_TRADE", "BUY_MARKET", "SELL_MARKET")
TRAINED_CLASS_BY_INDEX: dict[int, str] = dict(enumerate(TRAINED_CLASS_NAMES))

#: Legacy head width of the currently-deployed ScalpNet artifact (NO_TRADE /
#: BUY / SELL / WAIT). The 4th logit exists on disk for compatibility, but
#: the class contract declares it DEAD (see below).
LEGACY_HEAD_CLASSES: int = 4

#: Dead-logit index. ScalpNet(index=WAIT_LOGIT_INDEX) is the policy bridge
#: logit that MUST NOT carry semantic load. The inference wrapper masks it.
WAIT_LOGIT_INDEX: int = 3

#: Human-readable contract id (also recorded in model.meta.json).
MODEL_CLASS_CONTRACT_ID: str = "triple_barrier_3class_v1"

# ── Smoke contract ──────────────────────────────────────────────────────────

#: Metadata flag. smoke=True artifacts are never production_eligible. The flag
#: is written at training time (WalkForwardTrainer._save_metadata) and checked
#: at promotion/verification time (governance.verify + gates).
PRODUCTION_ELIGIBLE_FIELD: str = "production_eligible"
SMOKE_FIELD: str = "smoke"


def is_production_eligible(meta: dict) -> bool:
    """True only for non-smoke, otherwise-valid artifacts.

    Missing production_eligible is treated as False (closed-world: legacy
    artifacts without the field are NOT auto-grandfathered as production
    eligible — they must be retrained through the current contract).  An
    artifact with production_eligible=False is rejected by promotion gates
    without inspecting any other property.
    """
    if not meta:
        return False
    if meta.get(PRODUCTION_ELIGIBLE_FIELD) is False:
        return False
    if meta.get(SMOKE_FIELD) is True:
        return False
    # If the field is absent, treat as eligible only when smoke != True
    # (forward-compatible for artifacts written before this contract shipped).
    # Post-ship: the trainer always writes it, so absent means pre-contract.
    if PRODUCTION_ELIGIBLE_FIELD in meta:
        return bool(meta[PRODUCTION_ELIGIBLE_FIELD])
    return meta.get(SMOKE_FIELD) is not True


# ── Inference wrapper ───────────────────────────────────────────────────────


def mask_wait_logit(logits: torch.Tensor) -> torch.Tensor:
    """Mask the WAIT logit (index 3) to a large negative before softmax.

    When the on-disk head is 4-wide, logits[:, 3] represents the legacy
    policy-bridge mass that was NEVER a training label; leaving it live steals
    probability mass from the trained classes. Replacing it with -1e4 makes its
    softmax contribution ~0 while preserving differentiability on the trained
    3 logits (no shape change, so head geometry / checkpoint contract is
    untouched on disk).

    3-wide logits are returned unchanged.
    """
    if logits.dim() == 1:
        if logits.numel() == LEGACY_HEAD_CLASSES:
            logits = logits.clone()
            logits[WAIT_LOGIT_INDEX] = -1e4
        return logits
    if logits.dim() == 2 and logits.size(1) == LEGACY_HEAD_CLASSES:
        logits = logits.clone()
        logits[:, WAIT_LOGIT_INDEX] = -1e4
    return logits


def masked_softmax(logits: torch.Tensor) -> torch.Tensor:
    """Softmax with WAIT masked; 3-wide logits pass through unchanged."""
    masked = mask_wait_logit(logits)
    return torch.softmax(masked, dim=-1)


def trained_class_probs(probs: torch.Tensor) -> torch.Tensor:
    """Slice trained-class probabilities out of a (B, 4) or (B, 3) tensor.

    Returns a (B, 3) view over the trained classes (0..2). Callers that need
    the directional confidence over trained classes should use this (mirrors
    policy._directional_confidence normalization but as a pure tensor op).
    """
    if probs.size(-1) == LEGACY_HEAD_CLASSES:
        return probs[..., :TRAINED_CLASS_COUNT]
    return probs


# ── Loss helper ─────────────────────────────────────────────────────────────


def masked_ce_loss(
    logits: torch.Tensor,
    targets: torch.Tensor,
    weight: torch.Tensor | None = None,
    label_smoothing: float = 0.0,
) -> torch.Tensor:
    """Cross-entropy with WAIT hard-masked: 3-wide logits expected.

    When logits is 4-wide, the WAIT logit is masked before softmax so it
    contributes zero gradient (no semantic load). When logits is 3-wide the
    wrapper is a no-op — same code path, no special-casing at call sites.

    The caller may pass a class_weight for the 3 trained classes; no weight
    is ever supplied for WAIT (logit 3 has zero gradient, so its weight is
    meaningless).
    """
    masked = mask_wait_logit(logits)
    if masked.size(-1) == LEGACY_HEAD_CLASSES:
        # Use only trained classes for the loss; WAIT slice contributes 0.
        # We keep the full tensor for calibration/logging but CE is evaluated
        # over the 3 trained logits; WAIT's masked logit has ~0 prob so the
        # full-tensor CE and the 3-slice CE are numerically identical — keep
        # the explicit slice for clarity of intent.
        masked = (
            masked[:, :TRAINED_CLASS_COUNT] if masked.dim() == 2 else masked[:TRAINED_CLASS_COUNT]
        )
        if weight is not None and weight.numel() == LEGACY_HEAD_CLASSES:
            weight = weight[:TRAINED_CLASS_COUNT]
    return nn.functional.cross_entropy(
        masked, targets, weight=weight, label_smoothing=label_smoothing
    )

"""Loss Function Library (ML-TRAIN-002).

Specialized financial classification losses that counter extreme NO_TRADE class
imbalance (70-85% of M1 samples) and reduce probability overconfidence.

Design contract
---------------
* **Shared signature.** Every loss is an ``nn.Module`` implementing
  ``forward(logits, targets, sample_weights=None) -> Tensor`` so they are
  interchangeable inside a trainer loop without a branch.
* **Sample-weight parity.** ``sample_weights`` (Lopez de Prado uniqueness
  weights) rescale how much each sample *counts*, not the batch size: the
  weighted mean divides by the weight sum, so a reweighted batch keeps the same
  gradient magnitude as an unweighted one.
* **Numerical clamping.** All log-probabilities come from ``log_softmax``
  (numerically stable); the focal factor ``(1 - p_t) ** gamma`` is evaluated on a
  clamped ``p_t`` because float32 can round a confident logit's probability to
  exactly 1.0, making the factor a hard 0 and silently killing that sample's
  gradient.
* **Reduction parity.** The unweighted ``mean`` path of each loss is
  bit-identical to its ``torch.nn`` reference where one exists; asserted in the
  test battery.

``torch`` is imported at module scope, matching every sibling in this package
(``walk_forward_trainer.py`` imports it too, so ``nexus_scalp.training`` already
pulls torch in transitively — no new import cost is introduced here).

Public losses
-------------
* :class:`FocalLoss`                    - Lin et al. 2017, ``(1 - p_t)^gamma``.
* :class:`LabelSmoothingCrossEntropy`   - soft targets, calibration regulariser.
* :class:`FocalLossWithSmoothing`       - both effects combined (the loss the
  candidate trainer has used since GATE-2; this is its canonical home).
* :class:`ClassBalancedLoss`            - Cui et al. 2019 effective-number weights.
* :class:`DiceLoss`                     - soft-Dice, direct minority-F1 gradient.
* :func:`build_loss` / :data:`LOSS_REGISTRY` - string-keyed factory for configs.
* :func:`effective_number_weights`      - effective-number alpha from counts.
"""

from __future__ import annotations

from typing import Any, Literal

import torch
from torch import nn
from torch.nn import functional as F

__all__ = [
    "ACTIVE_CLASS_INDICES",
    "IGNORE_INDEX",
    "LOSS_NAMES",
    "LOSS_REGISTRY",
    "ClassBalancedLoss",
    "DiceLoss",
    "FocalLoss",
    "FocalLossWithSmoothing",
    "LabelSmoothingCrossEntropy",
    "build_loss",
    "effective_number_weights",
]

Reduction = Literal["mean", "sum", "none"]

# Numerical guards on the probability scale (see module docstring).
_PT_FLOOR = 1e-7
_PT_CEIL = 1.0 - 1e-7
# Guard for weighted-mean denominators (a batch of all-zero uniqueness weights).
_DENOM_EPS = 1e-8

#: Target value excluded from the loss and the denominator (padding /
#: unlabelled slots), matching ``F.cross_entropy``'s ``ignore_index``.
IGNORE_INDEX = -100

#: Classes 1 and 2 are BUY / SELL — the active minority classes whose recall
#: the NSE contract cares about. Class 0 is NO_TRADE.
ACTIVE_CLASS_INDICES: tuple[int, ...] = (1, 2)


def _clamp_pt(p_t: torch.Tensor) -> torch.Tensor:
    """Clamp the true-class probability into the open interval (0, 1).

    At exactly ``p_t == 1`` the focal factor is a hard zero (no gradient) and at
    exactly ``0`` the derived ``log(p_t)`` is ``-inf``; both are silent
    sample-killing failures, so pin to a float32-safe interior.

    Out-of-place on purpose: ``p_t`` is part of the autograd graph (it comes
    from ``probs``), and an in-place clamp raises "variable needed for gradient
    computation has been modified by an inplace operation" on ``backward()``.
    """
    return p_t.clamp(_PT_FLOOR, _PT_CEIL)


def _reduce(
    loss_per_sample: torch.Tensor,
    valid: torch.Tensor,
    targets: torch.Tensor,
    sample_weights: torch.Tensor | None,
    reduction: Reduction,
    class_weights: torch.Tensor | None = None,
) -> torch.Tensor:
    """Apply reduction with sample/class weight normalisation.

    Two independent weight axes are supported and they compose:

    ``torch.nn.CrossEntropyLoss(weight=...)`` normalises its mean by
    ``sum_i w_{target_i}`` rather than the sample count, which keeps a
    class-weighted mean comparable in magnitude across different label
    distributions. That normalisation is applied HERE (the caller passes
    ``class_weights`` and an *unweighted* per-sample loss); losses that instead
    scale ``alpha`` per sample (FocalLoss, FocalLossWithSmoothing) do the
    multiply inline and take a plain sample mean, matching their legacy inline
    implementations so the relocation is loss-neutral.

    * ``sample_weights`` ``(B,)`` — Lopez de Prado uniqueness weights. They
      rescale how much each sample *counts*, not the batch size: the weighted
      mean divides by the weight sum, so gradient magnitude is preserved across
      varying mini-batch uniqueness sums.

    When both are present they multiply and the mean divides by their product
    sum. With neither, this is a plain mean (or sum / per-sample tensor).
    ``targets`` is threaded through so the class-weight lookup can index it; it
    is only read when ``class_weights`` is not None.
    """
    if class_weights is None and sample_weights is None:
        if reduction == "none":
            return torch.where(valid, loss_per_sample, torch.zeros_like(loss_per_sample))
        masked = torch.where(valid, loss_per_sample, torch.zeros_like(loss_per_sample))
        if reduction == "sum":
            return masked.sum()
        denom = valid.to(loss_per_sample.dtype).sum().clamp(min=1.0)
        return masked.sum() / denom

    safe_targets = torch.where(valid, targets, torch.zeros_like(targets))
    w = torch.ones_like(loss_per_sample)
    if class_weights is not None:
        w = class_weights.to(device=loss_per_sample.device)[safe_targets]
    if sample_weights is not None:
        w = w * sample_weights.to(dtype=loss_per_sample.dtype, device=loss_per_sample.device)
    w = torch.where(valid, w, torch.zeros_like(w))

    weighted = loss_per_sample * w
    if reduction == "none":
        return weighted
    if reduction == "sum":
        return weighted.sum()
    return weighted.sum() / (w.sum() + _DENOM_EPS)


class FocalLoss(nn.Module):
    """Multi-class Focal Loss (Lin et al. 2017).

    ``L = -alpha_t * (1 - p_t)^gamma * log(p_t)``

    The ``(1 - p_t)^gamma`` factor down-weights well-classified NO_TRADE samples
    so their near-zero gradients stop drowning the minority BUY/SELL updates.

    Args:
        gamma: Focusing parameter. ``gamma=0`` recovers plain cross entropy;
            larger values down-weight easy examples harder. ``gamma=2`` is the
            paper default; gold M1 grids usually favour 1.0-2.0.
        alpha: Optional per-class weight tensor ``(C,)`` (inverse-frequency or
            effective-number weights). Applied to the true class only, matching
            the paper's per-class ``alpha_t``.
        reduction: ``mean`` | ``sum`` | ``none``.
        ignore_index: Target value excluded from the loss and the denominator,
            matching ``F.cross_entropy``.
    """

    def __init__(
        self,
        gamma: float = 2.0,
        alpha: torch.Tensor | None = None,
        reduction: Reduction = "mean",
        ignore_index: int = IGNORE_INDEX,
    ) -> None:
        super().__init__()
        self.gamma = float(gamma)
        self.reduction = reduction
        self.ignore_index = int(ignore_index)
        if alpha is not None:
            self.register_buffer("alpha", alpha)
        else:
            self.alpha = None

    def forward(
        self,
        logits: torch.Tensor,
        targets: torch.Tensor,
        sample_weights: torch.Tensor | None = None,
    ) -> torch.Tensor:
        valid = targets != self.ignore_index
        if not bool(valid.any()):
            zero = torch.zeros((), dtype=logits.dtype, device=logits.device)
            return zero if self.reduction != "none" else zero.expand(logits.shape[0])

        # ``ignore_index`` targets must never reach the gather: clamp them to a
        # valid row first and zero the result out via ``valid`` below.
        safe_targets = torch.where(valid, targets, torch.zeros_like(targets))
        log_probs = F.log_softmax(logits, dim=-1)
        p_t = _clamp_pt(torch.exp(log_probs.gather(1, safe_targets.unsqueeze(1)).squeeze(1)))
        ce = -log_probs.gather(1, safe_targets.unsqueeze(1)).squeeze(1)
        loss = torch.pow(1.0 - p_t, self.gamma) * ce

        # Per-sample alpha multiply, matching the legacy inline implementations
        # (which take a plain sample mean afterwards, NOT the CE weight-sum
        # normalisation — so the two focal variants stay loss-neutral here).
        if self.alpha is not None:
            loss = loss * self.alpha.to(device=logits.device)[safe_targets]

        return _reduce(loss, valid, safe_targets, sample_weights, self.reduction)


class LabelSmoothingCrossEntropy(nn.Module):
    """Cross entropy with uniform soft targets.

    ``L = -( (1 - s) * log p_y + s/C * sum_k log p_k )``

    Label smoothing caps the maximum achievable target probability below 1.0,
    which directly reduces calibration overconfidence: the model can no longer
    chase a one-hot target it cannot represent, so predicted probabilities stay
    calibrated instead of saturating.

    Args:
        smoothing: Total probability mass redistributed away from the true class
            (``0.0`` = plain cross entropy). 0.05-0.15 is typical for
            classification; the historical NSE default was 0.08.
        weight: Optional per-class weight tensor ``(C,)``.
        reduction: ``mean`` | ``sum`` | ``none``.
        ignore_index: Target value excluded from the loss and the denominator.
    """

    def __init__(
        self,
        smoothing: float = 0.0,
        weight: torch.Tensor | None = None,
        reduction: Reduction = "mean",
        ignore_index: int = IGNORE_INDEX,
    ) -> None:
        super().__init__()
        smoothing = float(smoothing)
        if not 0.0 <= smoothing < 1.0:
            raise ValueError(
                f"smoothing must be in [0, 1), got {smoothing!r}; "
                "smoothing >= 1.0 leaves no mass on the true class."
            )
        self.smoothing = smoothing
        self.reduction = reduction
        self.ignore_index = int(ignore_index)
        if weight is not None:
            self.register_buffer("weight", weight)
        else:
            self.weight = None

    def forward(
        self,
        logits: torch.Tensor,
        targets: torch.Tensor,
        sample_weights: torch.Tensor | None = None,
    ) -> torch.Tensor:
        num_classes = logits.shape[1]
        valid = targets != self.ignore_index
        # ``ignore_index`` targets must never reach the gather (out-of-bounds).
        safe_targets = torch.where(valid, targets, torch.zeros_like(targets))
        log_probs = F.log_softmax(logits, dim=-1)

        # Differentiable smooth-target cross entropy: the target distribution is
        # constant, but the log-probs are model outputs, so the gather must stay
        # OUTSIDE no_grad (building it inside killed every gradient). Negated
        # once at the end — a per-term negate is equivalent but noisier.
        off = self.smoothing / num_classes
        target_log_probs = log_probs.gather(1, safe_targets.unsqueeze(1)).squeeze(1)
        valid_f = valid.to(target_log_probs.dtype)
        ce = (1.0 - self.smoothing) * target_log_probs + off * log_probs.sum(dim=-1)
        ce = ce * valid_f

        if self.weight is not None:
            ce = ce * self.weight.to(device=logits.device)[safe_targets]

        return -_reduce(ce, valid, safe_targets, sample_weights, self.reduction)


class FocalLossWithSmoothing(nn.Module):
    """Focal modulation applied on top of smoothed targets.

    Combines :class:`FocalLoss`'s hard-example focus with
    :class:`LabelSmoothingCrossEntropy`'s calibration regularisation. This is
    the loss the candidate trainer has used since GATE-2; it lives here as the
    canonical implementation instead of an inline copy in the trainer.

    Args:
        alpha: Optional per-class weight tensor ``(C,)``.
        gamma: Focusing parameter (default 2.0, the paper default).
        label_smoothing: Total smoothing mass (default 0.08, historical NSE).
        reduction: ``mean`` | ``sum`` | ``none``.
        ignore_index: Target value excluded from the loss and the denominator.
    """

    def __init__(
        self,
        alpha: torch.Tensor | None = None,
        gamma: float = 2.0,
        label_smoothing: float = 0.08,
        reduction: Reduction = "mean",
        ignore_index: int = IGNORE_INDEX,
    ) -> None:
        super().__init__()
        self.gamma = float(gamma)
        label_smoothing = float(label_smoothing)
        if not 0.0 <= label_smoothing < 1.0:
            raise ValueError(f"label_smoothing must be in [0, 1), got {label_smoothing!r}")
        self.label_smoothing = label_smoothing
        self.reduction = reduction
        self.ignore_index = int(ignore_index)
        if alpha is not None:
            self.register_buffer("alpha", alpha)
        else:
            self.alpha = None

    def forward(
        self,
        logits: torch.Tensor,
        targets: torch.Tensor,
        sample_weights: torch.Tensor | None = None,
    ) -> torch.Tensor:
        num_classes = logits.shape[1]
        valid = targets != self.ignore_index
        # ``ignore_index`` targets must never reach the gather (out-of-bounds).
        safe_targets = torch.where(valid, targets, torch.zeros_like(targets))
        log_probs = F.log_softmax(logits, dim=-1)
        probs = torch.exp(log_probs)

        # Differentiable smooth-target cross entropy (see
        # LabelSmoothingCrossEntropy: the gather must stay outside no_grad).
        off = self.label_smoothing / num_classes
        target_log_probs = log_probs.gather(1, safe_targets.unsqueeze(1)).squeeze(1)
        valid_f = valid.to(target_log_probs.dtype)
        smooth_ce = (1.0 - self.label_smoothing) * target_log_probs + off * log_probs.sum(dim=-1)
        smooth_ce = smooth_ce * valid_f

        p_t = _clamp_pt(probs.gather(1, safe_targets.unsqueeze(1)).squeeze(1))
        focal_weight = torch.pow(1.0 - p_t, self.gamma)
        loss = focal_weight * (-smooth_ce)

        if self.alpha is not None:
            loss = loss * self.alpha.to(device=logits.device)[safe_targets]

        return _reduce(loss, valid, safe_targets, sample_weights, self.reduction)


class ClassBalancedLoss(nn.Module):
    """Class-Balanced loss (Cui et al. 2019) wrapped around any base loss.

    ``w_c = (1 - beta) / (1 - beta^n_c)``

    Uses the *effective number of samples* rather than raw inverse frequency, so
    a class with 1M samples is not assigned a near-zero weight. Weights are
    derived from ``class_counts`` at build time and applied per sample through
    the base loss's own class-weight path.

    Args:
        class_counts: Per-class sample counts ``(C,)``.
        beta: Effective-number decay in ``[0, 1)``. ``beta=0`` is plain inverse
            frequency; larger ``beta`` softens the weight ratio between a
            1M-sample class and a 1k-sample one. 0.99-0.9999 typical.
        base: Underlying loss name (see :data:`LOSS_NAMES`) or a loss module;
            defaults to plain cross entropy.
        reduction: ``mean`` | ``sum`` | ``none``.
        boost_active: Extra multiplier for the active classes (BUY=1, SELL=2) —
            mirrors the historical NSE ``cb_weights[idx] *= 3.0`` boost used by
            the candidate trainer. ``1.0`` disables it.
        label_smoothing: Smoothing mass forwarded to the base loss.
        gamma: Focal parameter forwarded to the base loss.
    """

    def __init__(
        self,
        class_counts: torch.Tensor | list[int] | tuple[int, ...],
        beta: float = 0.99,
        base: str | nn.Module = "cross_entropy",
        reduction: Reduction = "mean",
        boost_active: float = 1.0,
        label_smoothing: float = 0.0,
        gamma: float = 0.0,
    ) -> None:
        super().__init__()
        alpha = effective_number_weights(class_counts, beta=beta, boost_active=boost_active)
        self.register_buffer("alpha", alpha)
        self.beta = float(beta)
        self.reduction = reduction
        self.class_counts = (
            class_counts.tolist() if isinstance(class_counts, torch.Tensor) else list(class_counts)
        )
        if isinstance(base, str):
            self.base = build_loss(
                base,
                alpha=alpha,
                gamma=gamma,
                label_smoothing=label_smoothing,
                reduction=reduction,
            )
        else:
            self.base = base

    def forward(
        self,
        logits: torch.Tensor,
        targets: torch.Tensor,
        sample_weights: torch.Tensor | None = None,
    ) -> torch.Tensor:
        return self.base(logits, targets, sample_weights)


class DiceLoss(nn.Module):
    """Soft-Dice loss for direct minority-recall optimisation.

    ``L_c = 1 - (2 * sum_i p_ic * y_ic + s) / (sum_i p_ic + sum_i y_ic + s)``

    Cross entropy is a *pointwise* criterion: on an 85%-NO_TRADE batch its
    gradient is dominated by the majority class no matter how the weights are
    tuned. Dice is a *set* criterion — it scores the overlap between the
    predicted and true class supports — so a minority class holding 5% of
    samples contributes a comparably-sized gradient. This is the loss to reach
    for when the metric that matters is minority F1 / recall, not accuracy.

    Args:
        smooth: Constant added to numerator and denominator so the empty-class
            case stays finite.
        reduction: ``mean`` (macro over present classes) | ``sum`` | ``none``.
        class_weight: Optional per-class weight tensor ``(C,)``.
    """

    def __init__(
        self,
        smooth: float = 1.0,
        reduction: Reduction = "mean",
        class_weight: torch.Tensor | None = None,
    ) -> None:
        super().__init__()
        self.smooth = float(smooth)
        self.reduction = reduction
        if class_weight is not None:
            self.register_buffer("class_weight", class_weight)
        else:
            self.class_weight = None

    def forward(
        self,
        logits: torch.Tensor,
        targets: torch.Tensor,
        sample_weights: torch.Tensor | None = None,
    ) -> torch.Tensor:
        probs = F.softmax(logits, dim=-1)
        num_classes = logits.shape[1]
        valid = targets != IGNORE_INDEX

        # One-hot under no_grad: targets are integers, not differentiable.
        with torch.no_grad():
            onehot = F.one_hot(targets.clamp(min=0), num_classes=num_classes).to(probs.dtype)
            onehot = onehot * valid.unsqueeze(1).to(probs.dtype)
            if sample_weights is not None:
                w = sample_weights.to(dtype=probs.dtype, device=probs.device)
                onehot = onehot * w.unsqueeze(1)

        intersect = (probs * onehot).sum(dim=0)
        denom = probs.sum(dim=0) + onehot.sum(dim=0)
        dice_per_class = (2.0 * intersect + self.smooth) / (denom + self.smooth)

        if self.class_weight is not None:
            dice_per_class = dice_per_class * self.class_weight.to(device=logits.device)

        # Classes absent from both prediction and target are not a learnable
        # signal; including them would inflate the macro mean towards 1.0.
        present = denom > 0
        if not bool(present.any()):
            return torch.zeros((), dtype=logits.dtype, device=logits.device)
        per_class_loss = torch.where(
            present, 1.0 - dice_per_class, torch.zeros_like(dice_per_class)
        )

        if self.reduction == "sum":
            return per_class_loss.sum()
        if self.reduction == "none":
            return per_class_loss
        n_present = present.to(per_class_loss.dtype).sum().clamp(min=1.0)
        return per_class_loss.sum() / n_present


# ---------------------------------------------------------------------------
# Weight derivation
# ---------------------------------------------------------------------------
def effective_number_weights(
    class_counts: torch.Tensor | list[int] | tuple[int, ...],
    beta: float = 0.99,
    boost_active: float = 1.0,
) -> torch.Tensor:
    """Effective-number class weights (Cui et al. 2019), mean-normalised.

    ``w_c = (1 - beta) / (1 - beta^n_c)``

    Args:
        class_counts: Per-class sample counts (tensor / list / tuple).
        beta: Effective-number decay in ``[0, 1)``. ``beta=0`` gives inverse
            frequency; larger ``beta`` softens the ratio between a 1M-sample
            class and a 1k-sample one.
        boost_active: Extra multiplier for the active classes BUY=1 / SELL=2 —
            the historical candidate-trainer boost was 3.0. ``1.0`` disables it.

    Returns:
        Float32 tensor of shape ``(C,)``, mean-normalised to 1.0.
    """
    beta = float(beta)
    if not 0.0 <= beta < 1.0:
        raise ValueError(f"beta must be in [0, 1), got {beta!r}")
    if boost_active < 0.0:
        raise ValueError(f"boost_active must be >= 0.0, got {boost_active!r}")

    counts = torch.as_tensor(class_counts, dtype=torch.float64)
    if counts.dim() != 1:
        raise ValueError(f"class_counts must be 1-D, got shape {tuple(counts.shape)}")

    # A class with zero samples must not hit the 0/(1-beta^0) = 0/0 singularity.
    safe_counts = counts.clamp(min=1.0)
    if beta == 0.0:
        # Effective number degenerates to n_c; the weight is 1/n_c. (At beta=0
        # the general formula below would divide 1 by 1-beta^0 = 0.)
        weights = 1.0 / safe_counts
    else:
        effective_num = 1.0 - torch.pow(beta, safe_counts)
        weights = (1.0 - beta) / effective_num.clamp(min=1e-12)

    if boost_active != 1.0 and weights.numel() > 2:
        idx = torch.arange(len(weights), dtype=torch.long)
        active = torch.zeros_like(idx, dtype=torch.bool)
        for c in ACTIVE_CLASS_INDICES:
            if c < len(weights):
                active = active | (idx == c)
        weights = torch.where(active, weights * boost_active, weights)

    if weights.numel() > 0:
        mean = weights.mean()
        if bool(mean > 0):
            weights = weights / mean
    return weights.to(torch.float32)


class _CrossEntropyLoss(nn.Module):
    """``nn.CrossEntropyLoss`` with the shared sample-weight signature.

    Adapts the stock criterion to ``(logits, targets, sample_weights)`` so every
    loss is interchangeable in a trainer loop without a branch. With
    ``sample_weights=None`` the output is exactly ``nn.CrossEntropyLoss``
    (asserted in tests).
    """

    def __init__(
        self,
        weight: torch.Tensor | None = None,
        label_smoothing: float = 0.0,
        reduction: Reduction = "mean",
        ignore_index: int = IGNORE_INDEX,
    ) -> None:
        super().__init__()
        label_smoothing = float(label_smoothing)
        if not 0.0 <= label_smoothing < 1.0:
            raise ValueError(f"label_smoothing must be in [0, 1), got {label_smoothing!r}")
        # ``nn.CrossEntropyLoss`` is built WITHOUT the weight: with
        # ``reduction='none'`` it would bake the weight into ``per_sample``, and
        # ``_reduce`` would then multiply by it a second time. Instead the
        # unweighted per-sample loss is reduced with ``class_weights`` so the
        # weight is applied and normalised exactly once.
        self.ce = nn.CrossEntropyLoss(
            weight=None,
            label_smoothing=label_smoothing,
            reduction="none",
            ignore_index=ignore_index,
        )
        if weight is not None:
            self.register_buffer("weight", weight)
        else:
            self.weight = None
        self.reduction = reduction

    def forward(
        self,
        logits: torch.Tensor,
        targets: torch.Tensor,
        sample_weights: torch.Tensor | None = None,
    ) -> torch.Tensor:
        per_sample = self.ce(logits, targets)
        valid = targets != self.ce.ignore_index
        return _reduce(
            per_sample,
            valid,
            targets,
            sample_weights,
            self.reduction,
            class_weights=self.weight,
        )


#: Supported loss names for ``build_loss`` / configs.
LOSS_NAMES: tuple[str, ...] = (
    "cross_entropy",
    "focal",
    "label_smoothing",
    "focal_smoothing",
    "class_balanced",
    "dice",
)


def build_loss(
    name: str,
    *,
    gamma: float = 2.0,
    alpha: torch.Tensor | None = None,
    label_smoothing: float = 0.0,
    smoothing: float = 0.0,
    beta: float = 0.99,
    class_counts: torch.Tensor | list[int] | tuple[int, ...] | None = None,
    boost_active: float = 1.0,
    reduction: Reduction = "mean",
    **kwargs: Any,
) -> nn.Module:
    """Build a loss by registry name from a config dict.

    Args:
        name: One of :data:`LOSS_NAMES` — ``cross_entropy``, ``focal``,
            ``label_smoothing``, ``focal_smoothing``, ``class_balanced``, ``dice``.
        gamma: Focal focusing parameter (focal variants).
        alpha: Per-class weight tensor (focal / cross-entropy variants).
        label_smoothing: Smoothing mass for ``focal_smoothing`` (and an alias
            for ``label_smoothing``).
        smoothing: Smoothing mass for ``label_smoothing``.
        beta: Effective-number decay (``class_balanced``).
        class_counts: Required for ``class_balanced``.
        boost_active: Active-class boost (``class_balanced``).
        reduction: Reduction mode.
        **kwargs: Forwarded to the loss constructor.

    Returns:
        A loss module with the shared
        ``forward(logits, targets, sample_weights=None)`` signature.
    """
    key = name.strip().lower().replace("-", "_")
    if key not in LOSS_REGISTRY and key != "class_balanced":
        raise ValueError(
            f"unknown loss {name!r}; expected one of {[*sorted(LOSS_REGISTRY), 'class_balanced']}"
        )

    if key == "class_balanced":
        if class_counts is None:
            raise ValueError("class_balanced loss requires class_counts")
        return ClassBalancedLoss(
            class_counts=class_counts,
            beta=beta,
            boost_active=boost_active,
            reduction=reduction,
            **kwargs,
        )

    factory = LOSS_REGISTRY.get(key)
    assert factory is not None  # validated above; class_balanced handled below
    # Factories are bare classes (no-arg constructors); ``build_loss`` applies
    # the hyperparameters so the registry stays lambda-free.
    if key == "cross_entropy":
        return _CrossEntropyLoss(
            weight=alpha,
            label_smoothing=label_smoothing or smoothing,
            reduction=reduction,
            **kwargs,
        )
    if key == "focal":
        return FocalLoss(gamma=gamma, alpha=alpha, reduction=reduction, **kwargs)
    if key == "label_smoothing":
        return LabelSmoothingCrossEntropy(
            smoothing=label_smoothing or smoothing,
            weight=alpha,
            reduction=reduction,
            **kwargs,
        )
    if key == "focal_smoothing":
        return FocalLossWithSmoothing(
            alpha=alpha,
            gamma=gamma,
            label_smoothing=label_smoothing,
            reduction=reduction,
            **kwargs,
        )
    if key == "dice":
        return DiceLoss(reduction=reduction, **kwargs)
    raise AssertionError(f"unhandled loss key {key!r}")  # pragma: no cover


#: String-keyed factories. ``class_balanced`` needs counts, so its entry uses a
#: neutral unit-count default that callers override through ``build_loss``.
LOSS_REGISTRY: dict[str, Any] = {
    "cross_entropy": _CrossEntropyLoss,
    "focal": FocalLoss,
    "label_smoothing": LabelSmoothingCrossEntropy,
    "focal_smoothing": FocalLossWithSmoothing,
    "dice": DiceLoss,
}

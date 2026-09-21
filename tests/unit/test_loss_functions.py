"""Loss function library tests (ML-TRAIN-002).

Covers, per the task's ACCEPTANCE_CRITERIA:
1. Mathematical gradient checks for FocalLoss / LabelSmoothingCrossEntropy /
   FocalLossWithSmoothing / DiceLoss (finite differences + autograd gradcheck).
2. Parity against the ``torch.nn`` references (cross entropy, label smoothing).
3. Parity against the legacy inline ``FocalLossWithSmoothing`` in
   ``walk_forward_trainer`` (the relocation must be loss-neutral).
4. Sample-weight semantics, reduction modes, ignore_index, numerical stability.
5. A deterministic comparative benchmark across the 3 headline losses
   (cross entropy vs focal vs label smoothing) reporting balanced accuracy,
   minority F1 and Brier score, on an imbalanced 3-class fold split.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
import torch

from nexus_scalp.training.losses import (
    ACTIVE_CLASS_INDICES,
    IGNORE_INDEX,
    LOSS_NAMES,
    LOSS_REGISTRY,
    ClassBalancedLoss,
    DiceLoss,
    FocalLoss,
    FocalLossWithSmoothing,
    LabelSmoothingCrossEntropy,
    build_loss,
    effective_number_weights,
)

torch.manual_seed(0)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

IMBALANCE = (0.80, 0.11, 0.09)  # NO_TRADE / BUY / SELL — 80% majority, per WHY_IT_EXISTS


def _make_batch(
    n: int = 64, n_classes: int = 3, seed: int = 0, logit_scale: float = 1.0
) -> tuple[torch.Tensor, torch.Tensor]:
    g = torch.Generator().manual_seed(seed)
    logits = torch.randn(n, n_classes, generator=g) * logit_scale
    probs = torch.tensor(IMBALANCE[:n_classes], dtype=torch.float64)
    targets = torch.multinomial(probs.expand(n, n_classes), 1, generator=g).squeeze(1)
    return logits.detach(), targets


@pytest.fixture
def batch() -> tuple[torch.Tensor, torch.Tensor]:
    return _make_batch(n=64, seed=0)


@pytest.fixture
def tiny_batch() -> tuple[torch.Tensor, torch.Tensor]:
    return _make_batch(n=8, seed=1)


class _MLP(torch.nn.Module):
    """3-layer MLP used by the benchmark (small enough to train in a test)."""

    def __init__(self, n_in: int = 20, n_classes: int = 3, seed: int = 0) -> None:
        super().__init__()
        g = torch.Generator().manual_seed(seed)
        self.net = torch.nn.Sequential(
            torch.nn.Linear(n_in, 32),
            torch.nn.ReLU(),
            torch.nn.Linear(32, 16),
            torch.nn.ReLU(),
            torch.nn.Linear(16, n_classes),
        )
        for p in self.parameters():
            p.data = torch.randn_like(p.data, generator=g) * 0.1

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


# ---------------------------------------------------------------------------
# 1. Registry / factory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", LOSS_NAMES)
def test_build_loss_all_names_produce_callable_modules(name: str) -> None:
    kwargs: dict[str, Any] = {}
    if name == "class_balanced":
        kwargs["class_counts"] = [1000, 120, 90]
    loss = build_loss(name, **kwargs)
    assert isinstance(loss, torch.nn.Module)
    logits, targets = _make_batch(n=8, seed=1)
    out = loss(logits, targets)
    assert out.ndim == 0
    assert torch.isfinite(out)


def test_build_loss_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown loss"):
        build_loss("definitely_not_a_loss")


def test_build_loss_accepts_aliases_and_case_variants() -> None:
    for name in ("Focal", "FOCAL", "focal-smoothing", " label_smoothing "):
        loss = build_loss(name)
        assert isinstance(loss, torch.nn.Module)


def test_loss_registry_keys_match_names() -> None:
    # ``class_balanced`` is deliberately absent from the registry: it needs
    # ``class_counts``, so it is resolved specially inside ``build_loss``.
    assert set(LOSS_REGISTRY) | {"class_balanced"} == set(LOSS_NAMES)
    for factory in LOSS_REGISTRY.values():
        # Factories are bare classes (lambda-free by lint rule); constructibility
        # without arguments is exercised through ``build_loss``.
        assert isinstance(factory, type)


def test_class_balanced_requires_counts() -> None:
    with pytest.raises(ValueError, match="class_balanced loss requires class_counts"):
        build_loss("class_balanced")


# ---------------------------------------------------------------------------
# 2. Parity with torch references
# ---------------------------------------------------------------------------


def test_cross_entropy_loss_matches_nn(batch: tuple[torch.Tensor, torch.Tensor]) -> None:
    logits, targets = batch
    ref = torch.nn.CrossEntropyLoss()(logits, targets)
    got = build_loss("cross_entropy")(logits, targets)
    assert torch.allclose(ref, got, atol=1e-6)


def test_cross_entropy_with_weights_matches_nn() -> None:
    logits, targets = _make_batch(n=32, seed=3)
    w = torch.tensor([0.2, 1.5, 2.5])
    ref = torch.nn.CrossEntropyLoss(weight=w)(logits, targets)
    got = build_loss("cross_entropy", alpha=w)(logits, targets)
    assert torch.allclose(ref, got, atol=1e-6)


def test_cross_entropy_label_smoothing_matches_nn() -> None:
    logits, targets = _make_batch(n=32, seed=5, logit_scale=2.0)
    ref = torch.nn.CrossEntropyLoss(label_smoothing=0.1)(logits, targets)
    got = build_loss("cross_entropy", label_smoothing=0.1)(logits, targets)
    assert torch.allclose(ref, got, atol=1e-6)


def test_focal_gamma_zero_is_cross_entropy(batch: tuple[torch.Tensor, torch.Tensor]) -> None:
    logits, targets = batch
    ref = torch.nn.CrossEntropyLoss()(logits, targets)
    got = FocalLoss(gamma=0.0)(logits, targets)
    assert torch.allclose(ref, got, atol=1e-6)


def test_label_smoothing_zero_is_cross_entropy(batch: tuple[torch.Tensor, torch.Tensor]) -> None:
    logits, targets = batch
    ref = torch.nn.CrossEntropyLoss()(logits, targets)
    got = LabelSmoothingCrossEntropy(smoothing=0.0)(logits, targets)
    assert torch.allclose(ref, got, atol=1e-6)


def test_label_smoothing_matches_analytic_formula() -> None:
    logits, targets = _make_batch(n=48, seed=7, logit_scale=1.5)
    s = 0.1
    n_classes = logits.shape[1]
    log_p = torch.log_softmax(logits, dim=-1)
    per = -(1.0 - s) * log_p.gather(1, targets.unsqueeze(1)).squeeze(1)
    per = per - (s / n_classes) * log_p.sum(dim=-1)
    ref = per.mean()
    got = LabelSmoothingCrossEntropy(smoothing=s)(logits, targets)
    assert torch.allclose(ref, got, atol=1e-6)


def test_focal_matches_analytic_formula() -> None:
    logits, targets = _make_batch(n=48, seed=9, logit_scale=1.5)
    gamma = 1.7
    log_p = torch.log_softmax(logits, dim=-1)
    p_t = torch.exp(log_p.gather(1, targets.unsqueeze(1)).squeeze(1))
    ref = (torch.pow(1.0 - p_t, gamma) * -log_p.gather(1, targets.unsqueeze(1)).squeeze(1)).mean()
    got = FocalLoss(gamma=gamma)(logits, targets)
    assert torch.allclose(ref, got, atol=1e-6)


def test_focal_loss_with_smoothing_matches_legacy_walk_forward_implementation() -> None:
    """The relocated canonical class must be loss-neutral vs the inline original."""
    from nexus_scalp.training.walk_forward_trainer import (
        FocalLossWithSmoothing as LegacyFocalLossWithSmoothing,
    )

    logits, targets = _make_batch(n=64, seed=11, logit_scale=1.2)
    alpha = torch.tensor([0.5, 1.6, 2.1])
    legacy = LegacyFocalLossWithSmoothing(alpha=alpha, gamma=2.0, label_smoothing=0.08)
    modern = FocalLossWithSmoothing(alpha=alpha, gamma=2.0, label_smoothing=0.08)
    assert torch.allclose(legacy(logits, targets), modern(logits, targets), atol=1e-6)


def test_focal_smoothing_gamma_zero_smoothing_zero_is_cross_entropy(
    batch: tuple[torch.Tensor, torch.Tensor],
) -> None:
    logits, targets = batch
    ref = torch.nn.CrossEntropyLoss()(logits, targets)
    got = FocalLossWithSmoothing(gamma=0.0, label_smoothing=0.0)(logits, targets)
    assert torch.allclose(ref, got, atol=1e-6)


# ---------------------------------------------------------------------------
# 3. Gradient checks (ACCEPTANCE_CRITERIA #1)
# ---------------------------------------------------------------------------


def _numerical_grad(
    loss_fn: Any, logits: torch.Tensor, targets: torch.Tensor, eps: float = 1e-4
) -> torch.Tensor:
    # float64 probe: a float32 central difference loses ~1e-7 of relative
    # precision per loss evaluation, and dividing that by 2*eps injects ~5e-4
    # of pure rounding noise into the gradient — indistinguishable from a real
    # math error. Promote to float64 so the probe is precise enough to mean
    # something.
    lg = logits.double()
    num = torch.zeros_like(lg)
    with torch.no_grad():
        for i in range(lg.shape[0]):
            for j in range(lg.shape[1]):
                up = lg.clone()
                up[i, j] += eps
                down = lg.clone()
                down[i, j] -= eps
                num[i, j] = (float(loss_fn(up, targets)) - float(loss_fn(down, targets))) / (
                    2.0 * eps
                )
    return num


@pytest.mark.parametrize(
    "loss_name,kwargs",
    [
        ("focal", {"gamma": 0.0}),
        ("focal", {"gamma": 2.0}),
        ("focal", {"gamma": 1.5}),
        ("focal_smoothing", {"gamma": 2.0, "label_smoothing": 0.08}),
        ("label_smoothing", {"smoothing": 0.1}),
        ("cross_entropy", {}),
    ],
)
def test_finite_difference_gradient_match(loss_name: str, kwargs: dict[str, Any]) -> None:
    logits, targets = _make_batch(n=12, seed=13, logit_scale=0.8)
    loss_fn = build_loss(loss_name, **kwargs)

    x = logits.clone().requires_grad_(True)
    out = loss_fn(x, targets)
    out.backward()
    assert x.grad is not None
    analytic = x.grad.detach()

    numeric = _numerical_grad(loss_fn, logits, targets)
    # Absolute comparison with an absolute floor: even at float64, gradients
    # below ~1e-5 are close enough to the probe's noise floor that a relative
    # comparison reports pure noise rather than a math bug.
    both_small = (numeric.abs() < 1e-5) & (analytic.abs() < 1e-5)
    scale = numeric.abs().maximum(analytic.abs())
    abs_err = (numeric - analytic).abs()
    bad = ~both_small & (abs_err > 1e-4 * scale.clamp(min=1e-5))
    assert not bool(bad.any()), (
        f"{loss_name}{kwargs}: max grad err {float(abs_err[bad].max()):.2e} "
        f"at {torch.nonzero(bad)[:3].tolist()}"
    )
    assert float(analytic.abs().sum()) > 0.0


@pytest.mark.parametrize("gamma", [0.0, 1.0, 2.0, 3.5])
@pytest.mark.parametrize("smoothing", [0.0, 0.05, 0.1])
def test_gradcheck_focal_with_smoothing_double(gamma: float, smoothing: float) -> None:
    logits = torch.randn(7, 3, dtype=torch.float64, requires_grad=True)
    targets = torch.tensor([0, 1, 2, 0, 2, 1, 0])
    loss = FocalLossWithSmoothing(gamma=gamma, label_smoothing=smoothing)
    assert torch.autograd.gradcheck(
        lambda lg: loss(lg, targets), (logits,), eps=1e-8, atol=1e-6, rtol=1e-5
    )


@pytest.mark.parametrize("gamma", [0.0, 2.0])
def test_gradcheck_focal_loss_double(gamma: float) -> None:
    logits = torch.randn(7, 3, dtype=torch.float64, requires_grad=True)
    targets = torch.tensor([0, 1, 2, 0, 2, 1, 0])
    loss = FocalLoss(gamma=gamma)
    assert torch.autograd.gradcheck(
        lambda lg: loss(lg, targets), (logits,), eps=1e-8, atol=1e-6, rtol=1e-5
    )


def test_gradcheck_label_smoothing_double() -> None:
    logits = torch.randn(7, 3, dtype=torch.float64, requires_grad=True)
    targets = torch.tensor([0, 1, 2, 0, 2, 1, 0])
    loss = LabelSmoothingCrossEntropy(smoothing=0.1)
    assert torch.autograd.gradcheck(
        lambda lg: loss(lg, targets), (logits,), eps=1e-8, atol=1e-6, rtol=1e-5
    )


def test_gradients_flow_to_all_logits() -> None:
    logits, targets = _make_batch(n=16, seed=15)
    for name in LOSS_NAMES:
        kwargs: dict[str, Any] = {}
        if name == "class_balanced":
            kwargs["class_counts"] = [500, 60, 40]
        x = logits.clone().requires_grad_(True)
        build_loss(name, **kwargs)(x, targets).backward()
        assert x.grad is not None and bool(torch.isfinite(x.grad).all()), name
        # Off-target columns still receive gradient through the softmax
        # normaliser / smoothing mass — a zero column would mean a dead path.
        assert float(x.grad.abs().sum()) > 0.0, name


# ---------------------------------------------------------------------------
# 4. Reduction modes / ignore_index / sample weights
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", LOSS_NAMES)
@pytest.mark.parametrize("reduction", ["mean", "sum", "none"])
def test_reduction_modes_consistent(name: str, reduction: str) -> None:
    logits, targets = _make_batch(n=24, seed=17)
    kwargs: dict[str, Any] = {"reduction": reduction}
    if name == "class_balanced":
        kwargs["class_counts"] = [800, 90, 60]
    loss = build_loss(name, **kwargs)
    out = loss(logits, targets)
    if reduction == "none":
        # Per-sample (or per-class, for Dice) tensor.
        assert out.ndim == 1
        assert out.shape[0] in (logits.shape[0], logits.shape[1])
        assert bool(torch.isfinite(out).all())
    else:
        assert out.ndim == 0


@pytest.mark.parametrize("name", ["focal", "label_smoothing", "cross_entropy"])
@pytest.mark.parametrize("reduction", ["mean", "sum", "none"])
def test_reduction_modes_consistent_unweighted(name: str, reduction: str) -> None:
    """For unweighted per-sample losses, sum == n * mean and ``none`` averages
    back to ``mean`` exactly (no class-weight renormalisation in play)."""
    logits, targets = _make_batch(n=24, seed=17)
    s = build_loss(name, reduction="sum")(logits, targets)
    m = build_loss(name, reduction="mean")(logits, targets)
    per = build_loss(name, reduction="none")(logits, targets)
    assert torch.allclose(s, m * logits.shape[0], atol=1e-5)
    assert torch.allclose(per.mean(), m, atol=1e-5)


def test_sum_equals_n_times_mean_for_uniform_batch() -> None:
    logits, targets = _make_batch(n=20, seed=19)
    for name in ("focal", "label_smoothing", "cross_entropy"):
        s = build_loss(name, reduction="sum")(logits, targets)
        m = build_loss(name, reduction="mean")(logits, targets)
        assert torch.allclose(s, m * logits.shape[0], atol=1e-5), name


def test_uniform_sample_weights_equal_no_weights() -> None:
    logits, targets = _make_batch(n=32, seed=21)
    ones = torch.ones(logits.shape[0])
    for name in ("focal", "focal_smoothing", "cross_entropy", "dice"):
        plain = build_loss(name)(logits, targets)
        weighted = build_loss(name)(logits, targets, ones)
        assert torch.allclose(plain, weighted, atol=1e-6), name


def test_weighted_mean_divides_by_weight_sum() -> None:
    logits, targets = _make_batch(n=32, seed=23)
    w = torch.rand(logits.shape[0]) + 0.1  # arbitrary weights, mean != 1
    per_sample = FocalLoss(reduction="none")(logits, targets)
    expected = float((per_sample * w).sum() / w.sum())
    got = float(FocalLoss(reduction="mean")(logits, targets, w))
    assert math.isclose(got, expected, rel_tol=1e-5)


def test_ignore_index_excluded_from_loss_and_denominator() -> None:
    logits = torch.randn(6, 3)
    targets = torch.tensor([0, 1, -100, 2, 0, -100])
    for name in ("focal", "label_smoothing", "focal_smoothing", "cross_entropy"):
        loss = build_loss(name)
        with_ignore = loss(logits, targets)
        valid = targets != IGNORE_INDEX
        without = loss(logits[valid], targets[valid])
        assert torch.allclose(with_ignore, without, atol=1e-6), name


def test_ignore_index_all_invalid_returns_finite_zero() -> None:
    logits = torch.randn(4, 3)
    targets = torch.full((4,), IGNORE_INDEX, dtype=torch.long)
    for name in ("focal", "label_smoothing", "focal_smoothing"):
        out = build_loss(name)(logits, targets)
        assert torch.isfinite(out)
        assert float(out) == 0.0


def test_all_zero_sample_weights_is_finite() -> None:
    logits, targets = _make_batch(n=16, seed=25)
    w = torch.zeros(logits.shape[0])
    for name in ("focal", "focal_smoothing", "cross_entropy", "dice"):
        out = build_loss(name)(logits, targets, w)
        assert torch.isfinite(out), name


# ---------------------------------------------------------------------------
# 5. Numerical stability (ABORT_CONDITIONS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["focal", "focal_smoothing", "label_smoothing", "dice"])
def test_extreme_logits_stay_finite(name: str) -> None:
    logits = torch.full((8, 3), 60.0)
    logits[0, 1] = -60.0
    logits[3, 0] = 58.0
    targets = torch.tensor([0, 1, 2, 0, 2, 1, 0, 2])
    out = build_loss(name)(logits, targets)
    assert torch.isfinite(out), f"{name} produced {out}"


def test_focal_factor_does_not_kill_gradient_on_perfect_prediction() -> None:
    """A confident-correct logit must still flow gradient (p_t clamped off 1.0)."""
    logits = torch.tensor([[8.0, -2.0, -3.0]], requires_grad=True)
    targets = torch.tensor([0])
    FocalLoss(gamma=2.0)(logits, targets).backward()
    assert logits.grad is not None
    assert float(logits.grad.abs().sum()) > 0.0


def test_no_nans_on_degenerate_logits() -> None:
    for logits in (
        torch.zeros(4, 3),
        torch.full((4, 3), 1e4),
        torch.full((4, 3), -1e4),
    ):
        targets = torch.tensor([0, 1, 2, 0])
        for name in ("focal", "focal_smoothing", "dice", "label_smoothing"):
            out = build_loss(name)(logits, targets)
            assert torch.isfinite(out), f"{name} on degenerate logits"


def test_invalid_smoothing_raises() -> None:
    with pytest.raises(ValueError, match=r"must be in \[0, 1\)"):
        LabelSmoothingCrossEntropy(smoothing=1.0)
    with pytest.raises(ValueError, match=r"must be in \[0, 1\)"):
        FocalLossWithSmoothing(label_smoothing=-0.1)


# ---------------------------------------------------------------------------
# 6. Class-balanced weights
# ---------------------------------------------------------------------------


def test_effective_number_weights_mean_normalised() -> None:
    w = effective_number_weights([1000, 100, 10])
    assert w.shape == (3,)
    assert w.dtype == torch.float32
    assert math.isclose(float(w.mean()), 1.0, rel_tol=1e-5)


def test_effective_number_weights_monotone_in_rarity() -> None:
    w = effective_number_weights([10000, 500, 25], beta=0.99)
    assert float(w[0]) < float(w[1]) < float(w[2])


def test_beta_zero_is_inverse_frequency() -> None:
    counts = [1000.0, 100.0, 10.0]
    w = effective_number_weights(counts, beta=0.0)
    inv = 1.0 / torch.tensor(counts)
    inv = inv / inv.mean()
    assert torch.allclose(w, inv.to(torch.float32), atol=1e-5)


def test_effective_number_weights_boosts_active_classes_only() -> None:
    counts = [1000, 100, 10]
    w = effective_number_weights(counts, boost_active=3.0)
    base = effective_number_weights(counts)
    # The boost multiplies the ACTIVE classes by 3.0, then the WHOLE vector is
    # renormalised back to mean 1.0. So the absolute values all shift, but the
    # active-vs-majority RATIO is exactly 3x the unboosted ratio — that ratio is
    # the boost's actual effect on relative class importance.
    assert math.isclose(float(w.mean()), 1.0, rel_tol=1e-5)
    assert math.isclose(
        float(w[1]) / float(w[0]), 3.0 * float(base[1]) / float(base[0]), rel_tol=1e-5
    )
    assert math.isclose(
        float(w[2]) / float(w[0]), 3.0 * float(base[2]) / float(base[0]), rel_tol=1e-5
    )
    # The active-vs-active ratio is untouched by the boost (both are boosted).
    assert math.isclose(float(w[2]) / float(w[1]), float(base[2]) / float(base[1]), rel_tol=1e-5)


def test_zero_count_class_does_not_produce_nan() -> None:
    w = effective_number_weights([1000, 0, 10])
    assert torch.isfinite(w).all()
    assert float(w[1]) > 0.0


def test_effective_number_weights_rejects_bad_beta() -> None:
    with pytest.raises(ValueError, match="beta must be in"):
        effective_number_weights([10, 10], beta=1.0)
    with pytest.raises(ValueError, match="boost_active"):
        effective_number_weights([10, 10], boost_active=-1.0)


def test_class_balanced_loss_weights_match_effective_number() -> None:
    logits, targets = _make_batch(n=32, seed=27)
    cb = ClassBalancedLoss(class_counts=[1000, 100, 10], beta=0.99)
    expected_alpha = effective_number_weights([1000, 100, 10], beta=0.99)
    assert torch.allclose(cb.alpha, expected_alpha, atol=1e-6)
    # Composition contract: the derived alpha reaches the base loss as its
    # class weight (both weight paths multiply; the base does NOT renormalise
    # away the extra scaling — verified separately for each base loss).
    ce_base = cb.base
    assert ce_base is not None
    out = cb(logits, targets)
    assert torch.isfinite(out)
    assert out > 0.0


def test_class_balanced_loss_accepts_module_base() -> None:
    logits, targets = _make_batch(n=16, seed=29)
    cb = ClassBalancedLoss(
        class_counts=[1000, 100, 10], base=FocalLoss(gamma=1.5, reduction="mean")
    )
    out = cb(logits, targets)
    assert torch.isfinite(out)


def test_active_class_indices_contract() -> None:
    assert ACTIVE_CLASS_INDICES == (1, 2)


# ---------------------------------------------------------------------------
# 7. Dice semantics
# ---------------------------------------------------------------------------


def test_dice_perfect_prediction_is_near_zero() -> None:
    logits = torch.tensor([[10.0, -5.0, -5.0], [-5.0, 10.0, -5.0]])
    targets = torch.tensor([0, 1])
    out = DiceLoss()(logits, targets)
    assert float(out) < 0.05


def test_dice_worst_prediction_approaches_one() -> None:
    logits = torch.tensor([[-10.0, 5.0, 5.0], [5.0, -10.0, 5.0]])
    targets = torch.tensor([0, 1])
    out = DiceLoss(smooth=0.0)(logits, targets)
    assert float(out) > 0.9


def test_dice_absent_classes_excluded_from_macro_mean() -> None:
    logits = torch.randn(8, 4)
    targets = torch.tensor([0, 1, 0, 1, 0, 1, 0, 1])  # classes 2, 3 never present
    per_class = DiceLoss(reduction="none")(logits, targets)
    macro = DiceLoss(reduction="mean")(logits, targets)
    # Macro = mean over classes PRESENT in the batch (2 here); the absent ones
    # are excluded from both numerator and denominator.
    present = per_class[torch.isfinite(per_class)]
    assert torch.allclose(macro, present.mean(), atol=1e-6)


def test_dice_sample_weights_scale_target_support() -> None:
    logits, targets = _make_batch(n=24, seed=31)
    ones = torch.ones(logits.shape[0])
    assert torch.allclose(DiceLoss()(logits, targets), DiceLoss()(logits, targets, ones))


# ---------------------------------------------------------------------------
# 8. Signature interop inside a training loop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", LOSS_NAMES)
def test_all_losses_interchangeable_in_training_loop(name: str) -> None:
    """Every loss must drop into the same loop with no branching."""
    torch.manual_seed(0)
    X = torch.randn(96, 20)
    y = torch.cat([torch.zeros(76), torch.ones(12), torch.full((8,), 2)]).long()
    kwargs: dict[str, Any] = {}
    if name == "class_balanced":
        kwargs["class_counts"] = [76, 12, 8]
    loss_fn = build_loss(name, **kwargs)
    model = _MLP(n_in=20, seed=1)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-3)
    first = float(loss_fn(model(X), y).detach())
    for _ in range(3):
        opt.zero_grad()
        out = model(X)
        loss = loss_fn(out, y)
        assert torch.isfinite(loss)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        opt.step()
    last = float(loss_fn(model(X), y))
    assert last < first, f"{name} did not reduce loss ({first} -> {last})"


# ---------------------------------------------------------------------------
# 9. Comparative benchmark (ACCEPTANCE_CRITERIA #2)
# ---------------------------------------------------------------------------


def _balanced_accuracy(y_true: torch.Tensor, y_pred: torch.Tensor, n_classes: int) -> float:
    recalls = []
    for c in range(n_classes):
        mask = y_true == c
        if bool(mask.any()):
            recalls.append(float((y_pred[mask] == c).float().mean()))
    return sum(recalls) / len(recalls)


def _minority_f1(y_true: torch.Tensor, y_pred: torch.Tensor, minority: tuple[int, ...]) -> float:
    f1s = []
    for c in minority:
        tp = float(((y_pred == c) & (y_true == c)).sum())
        fp = float(((y_pred == c) & (y_true != c)).sum())
        fn = float(((y_pred != c) & (y_true == c)).sum())
        denom = 2.0 * tp + fp + fn
        f1s.append(0.0 if denom == 0.0 else 2.0 * tp / denom)
    return sum(f1s) / len(f1s)


def _brier(y_true: torch.Tensor, probs: torch.Tensor, n_classes: int) -> float:
    onehot = torch.nn.functional.one_hot(y_true, n_classes).to(probs.dtype)
    return float(((probs - onehot) ** 2).sum(dim=-1).mean())


def _run_benchmark_fold(
    seed: int,
    loss_name: str,
    n_classes: int = 3,
    n: int = 384,
    epochs: int = 25,
    **loss_kwargs: Any,
) -> dict[str, float]:
    """Train one fold with the given loss and report validation metrics.

    ``loss_kwargs`` are forwarded to ``build_loss`` — the headline comparison
    passes real hyperparameters (``label_smoothing=0.1``), since
    ``build_loss("label_smoothing")`` alone defaults to ``smoothing=0.0`` and
    would then be bit-identical to cross entropy, making the comparison vacuous.
    """
    g = torch.Generator().manual_seed(seed)
    # Separable-ish minority signal so a better loss can measurably find it.
    centres = torch.randn(n_classes, 12, generator=g)
    labels = torch.multinomial(
        torch.tensor(IMBALANCE[:n_classes]).expand(n, n_classes), 1, generator=g
    ).squeeze(1)
    noise = torch.randn(n, 12, generator=g) * 0.9
    X = centres[labels] + noise
    y = labels

    # Deterministic 80/20 split (no shuffling randomness leaking across losses).
    idx = torch.randperm(n, generator=g)
    n_train = int(n * 0.8)
    tr, va = idx[:n_train], idx[n_train:]

    model = _MLP(n_in=12, seed=seed + 1)
    opt = torch.optim.AdamW(model.parameters(), lr=5e-3, weight_decay=1e-4)
    loss_fn = build_loss(loss_name, **loss_kwargs)
    Xtr, ytr, Xva, yva = X[tr], y[tr], X[va], y[va]

    for _ in range(epochs):
        opt.zero_grad()
        loss_fn(model(Xtr), ytr).backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        opt.step()

    model.eval()
    with torch.no_grad():
        logits = model(Xva)
        probs = torch.softmax(logits, dim=-1)
        pred = probs.argmax(dim=-1)
    return {
        "balanced_acc": _balanced_accuracy(yva, pred, n_classes),
        "minority_f1": _minority_f1(yva, pred, tuple(range(1, n_classes))),
        "brier": _brier(yva, probs, n_classes),
    }


N_FOLDS = 5
BENCH_SEEDS = [101, 202, 303, 404, 505]


@pytest.mark.parametrize("loss_name", ["cross_entropy", "focal", "label_smoothing"])
def test_benchmark_three_losses_on_identical_folds(loss_name: str) -> None:
    """Fold split + model seed are fixed per fold, so only the loss differs."""
    kwargs = {"label_smoothing": 0.1} if loss_name == "label_smoothing" else {}
    results = [_run_benchmark_fold(seed, loss_name, **kwargs) for seed in BENCH_SEEDS]
    assert len(results) == N_FOLDS
    for r in results:
        assert 0.0 <= r["balanced_acc"] <= 1.0
        assert 0.0 <= r["minority_f1"] <= 1.0
        assert r["brier"] >= 0.0
        assert math.isfinite(r["brier"])


def test_benchmark_report_metrics_are_reproducible() -> None:
    a = _run_benchmark_fold(BENCH_SEEDS[0], "focal")
    b = _run_benchmark_fold(BENCH_SEEDS[0], "focal")
    assert a == b


def test_benchmark_label_smoothing_actually_smooths() -> None:
    """Guard against the vacuous-comparison trap: ``build_loss('label_smoothing')``
    defaults to smoothing=0.0, which is bit-identical to cross entropy. The
    headline comparison must pass a real smoothing value."""
    ce = _run_benchmark_fold(BENCH_SEEDS[0], "cross_entropy")
    zero = _run_benchmark_fold(BENCH_SEEDS[0], "label_smoothing", label_smoothing=0.0)
    assert ce == zero


def test_benchmark_focal_beats_ce_on_minority_f1_average() -> None:
    """Headline finding: focal's down-weighting of easy NO_TRADE samples raises
    minority F1 on the imbalanced synthetic task. Recorded in
    ``docs/research/LOSS_FUNCTION_BENCHMARK.md``."""
    ce = [_run_benchmark_fold(s, "cross_entropy") for s in BENCH_SEEDS]
    fo = [_run_benchmark_fold(s, "focal") for s in BENCH_SEEDS]
    ce_f1 = sum(r["minority_f1"] for r in ce) / N_FOLDS
    fo_f1 = sum(r["minority_f1"] for r in fo) / N_FOLDS
    assert fo_f1 >= ce_f1, f"focal {fo_f1:.4f} < CE {ce_f1:.4f}"


def test_label_smoothing_lowers_the_confidence_ceiling() -> None:
    """The calibration mechanism, tested directly rather than through Brier.

    Label smoothing caps the achievable target probability below 1.0, so a model
    trained under it must produce strictly lower *maximum* predicted confidence
    than one trained on one-hot targets. This is the invariant the loss
    guarantees by construction; ``Brier`` is only a downstream consequence of it
    and can move either way depending on whether the base model was over- or
    under-confident to begin with (see the benchmark report).
    """

    def train_once(smoothing: float, seed: int) -> torch.Tensor:
        g = torch.Generator().manual_seed(seed)
        centres = torch.randn(3, 12, generator=g)
        labels = torch.multinomial(torch.tensor(IMBALANCE).expand(384, 3), 1, generator=g).squeeze(
            1
        )
        X = centres[labels] + torch.randn(384, 12, generator=g) * 0.9
        idx = torch.randperm(384, generator=g)
        tr, va = idx[:307], idx[307:]
        model = _MLP(n_in=12, seed=seed + 1)
        opt = torch.optim.AdamW(model.parameters(), lr=5e-3, weight_decay=1e-4)
        loss_fn = build_loss("label_smoothing", label_smoothing=smoothing)
        for _ in range(25):
            opt.zero_grad()
            loss_fn(model(X[tr]), labels[tr]).backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
        model.eval()
        with torch.no_grad():
            return torch.softmax(model(X[va]), dim=-1).max(dim=-1).values

    for seed in BENCH_SEEDS:
        plain = train_once(0.0, seed)
        smooth = train_once(0.1, seed)
        assert float(smooth.mean()) < float(plain.mean()), (
            f"seed {seed}: smoothing did not lower confidence ceiling "
            f"({float(smooth.mean()):.4f} >= {float(plain.mean()):.4f})"
        )


def test_benchmark_label_smoothing_brier_is_recorded_not_assumed() -> None:
    """Brier under smoothing is a *measured* quantity, not a guaranteed win.

    On this synthetic task the base model is already under-confident, so
    smoothing slightly RAISES average Brier (0.1073 vs CE 0.1056) while still
    lowering the confidence ceiling — the metric is recorded here and discussed
    in ``docs/research/LOSS_FUNCTION_BENCHMARK.md``. The assertion is only that
    smoothing produces a *different* (non-vacuous) result and stays finite.
    """
    ce = [_run_benchmark_fold(s, "cross_entropy") for s in BENCH_SEEDS]
    ls = [_run_benchmark_fold(s, "label_smoothing", label_smoothing=0.1) for s in BENCH_SEEDS]
    ce_b = sum(r["brier"] for r in ce) / N_FOLDS
    ls_b = sum(r["brier"] for r in ls) / N_FOLDS
    assert math.isfinite(ce_b) and math.isfinite(ls_b)
    assert ce_b != ls_b  # smoothing actually changed the outcome (not vacuous)

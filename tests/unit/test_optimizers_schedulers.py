"""ML-TRAIN-003 — Optimizer & LR-Schedule factory tests.

Covers the acceptance criteria:
  1. Optimizer factory cleanly instantiates AdamW and all schedulers.
  2. Unit tests confirm LR decays according to schedule WITHOUT warnings.

Plus the behaviors that make the factory safe to wire into the trainers:
config validation, step-cadence correctness (batch vs epoch), warmup ramp,
Lookahead math, and end-to-end convergence on a real task.
"""

from __future__ import annotations

import math
import warnings
from typing import Any

import pytest

torch = pytest.importorskip("torch")

from nexus_scalp.training.optimizers import (  # noqa: E402
    BASE_OPTIMIZER_CLASSES,
    LOOKAHEAD_BASE_NAME,
    OPTIMIZER_NAMES,
    SCHEDULER_NAMES,
    Lookahead,
    OptimizerConfigError,
    WarmupScheduler,
    build_optimizer_and_scheduler,
    current_lrs,
    normalize_config,
    step_scheduler,
)


def _model(seed: int = 0) -> Any:
    torch.manual_seed(seed)
    return torch.nn.Sequential(torch.nn.Linear(12, 8), torch.nn.ReLU(), torch.nn.Linear(8, 3))


@pytest.fixture
def model() -> Any:
    return _model(0)


@pytest.fixture
def task() -> Any:
    """A tiny learnable 3-class task (linearly separable by design)."""
    torch.manual_seed(7)
    g = torch.Generator().manual_seed(123)
    x = torch.randn(96, 12, generator=g)
    w = torch.randn(12, 3, generator=g)
    y = (x @ w).argmax(dim=1)
    return x, y


def _advance(bundle: Any, n: int = 1, metric: float | None = None) -> None:
    """Advance the schedule in CANONICAL order: optimizer.step() then scheduler.

    PyTorch raises ``UserWarning: Detected call of lr_scheduler.step() before
    optimizer.step()`` when a scheduler is stepped without a preceding optimizer
    step. Driving every LR probe through here keeps the ordering correct, which
    is what acceptance criterion 2 ("LR decays without warnings") actually
    requires — and it mirrors the cadence the trainers use.
    """
    for _ in range(n):
        bundle["optimizer"].step()
        step_scheduler(bundle, metrics=metric)


def _train(
    bundle: Any,
    model: Any,
    x: Any,
    y: Any,
    *,
    epochs: int,
    batch_size: int,
    val_fn: Any | None = None,
) -> list[float]:
    """Run the exact cadence the bundle declares: batch or epoch stepping.

    ``val_fn`` returns the epoch metric (val loss) that ReduceLROnPlateau
    consumes; when it is None the caller must only use non-plateau schedules.
    """
    history: list[float] = []
    n = x.shape[0]
    for _ep in range(epochs):
        model.train()
        for start in range(0, n, batch_size):
            xb = x[start : start + batch_size]
            yb = y[start : start + batch_size]
            bundle["optimizer"].zero_grad(set_to_none=True)
            loss = torch.nn.functional.cross_entropy(model(xb), yb)
            loss.backward()
            bundle["optimizer"].step()
            if bundle["scheduler_step_every_batch"]:
                step_scheduler(bundle)
        if not bundle["scheduler_step_every_batch"]:
            metric = val_fn() if val_fn is not None else float(loss.detach())
            step_scheduler(bundle, metrics=metric)
        history.append(float(loss.detach()))
    return history


# ---------------------------------------------------------------------------
# 1. Factory instantiation (acceptance criterion 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("opt_name", OPTIMIZER_NAMES)
def test_factory_instantiates_every_optimizer(model: Any, opt_name: str) -> None:
    bundle = build_optimizer_and_scheduler(model, {"optimizer": opt_name})
    assert bundle["optimizer"] is not None
    assert bundle["config"]["optimizer"] == opt_name
    # every optimizer must expose usable param groups
    assert len(bundle["optimizer"].param_groups) >= 1
    assert "lr" in bundle["optimizer"].param_groups[0]


@pytest.mark.parametrize("sched_name", SCHEDULER_NAMES)
def test_factory_instantiates_every_scheduler(model: Any, sched_name: str) -> None:
    bundle = build_optimizer_and_scheduler(
        model, {"scheduler": sched_name}, epochs=6, steps_per_epoch=4
    )
    if sched_name == "none":
        assert bundle["scheduler"] is None
    else:
        assert bundle["scheduler"] is not None


def test_default_config_reproduces_walk_forward_baseline(model: Any) -> None:
    """DEFAULT must equal the historical recipe: AdamW 5e-4 wd 1e-4 + cosine."""
    bundle = build_optimizer_and_scheduler(model, None, epochs=10)
    cfg = bundle["config"]
    assert cfg["optimizer"] == "adamw"
    assert cfg["learning_rate"] == pytest.approx(5e-4)
    assert cfg["weight_decay"] == pytest.approx(1e-4)
    assert cfg["scheduler"] == "cosine"
    assert type(bundle["optimizer"]).__name__ == BASE_OPTIMIZER_CLASSES["adamw"]
    assert type(bundle["scheduler"]).__name__ == "CosineAnnealingLR"


def test_optimizer_kind_matches_config_name(model: Any) -> None:
    assert (
        type(build_optimizer_and_scheduler(model, {"optimizer": "sgd"})["optimizer"]).__name__
        == BASE_OPTIMIZER_CLASSES["sgd"]
    )
    # plain adam with wd>0 routes to AdamW (decoupled decay correctness).
    # NOTE: DEFAULT_OPTIMIZER_CONFIG carries weight_decay=1e-4, so a bare
    # {"optimizer": "adam"} inherits decay and also routes to AdamW — the
    # explicit-zero case below is what pins the genuine Adam construction.
    assert (
        type(
            build_optimizer_and_scheduler(model, {"optimizer": "adam", "weight_decay": 0.01})[
                "optimizer"
            ]
        ).__name__
        == BASE_OPTIMIZER_CLASSES["adamw"]
    )
    assert (
        type(
            build_optimizer_and_scheduler(model, {"optimizer": "adam", "weight_decay": 0.0})[
                "optimizer"
            ]
        ).__name__
        == BASE_OPTIMIZER_CLASSES["adam"]
    )


def test_lookahead_wraps_documented_base(model: Any) -> None:
    bundle = build_optimizer_and_scheduler(model, {"optimizer": "lookahead", "lookahead_k": 3})
    opt = bundle["optimizer"]
    assert isinstance(opt, Lookahead)
    assert type(opt.base_optimizer).__name__ == LOOKAHEAD_BASE_NAME
    assert opt.k == 3
    assert opt.base_optimizer.param_groups is opt.param_groups


def test_optimizer_config_snapshot_is_normalized(model: Any) -> None:
    """Names are lowercased/validated and epochs fall in from the caller."""
    cfg = build_optimizer_and_scheduler(
        model, {"optimizer": "AdamW", "scheduler": "COSINE"}, epochs=17
    )["config"]
    assert cfg["optimizer"] == "adamw"
    assert cfg["scheduler"] == "cosine"
    assert cfg["epochs"] == 17
    # explicit config epochs win over the caller epochs
    cfg2 = build_optimizer_and_scheduler(model, {"epochs": 4}, epochs=40)["config"]
    assert cfg2["epochs"] == 4


# ---------------------------------------------------------------------------
# 2. LR decay behavior, warning-free (acceptance criterion 2)
# ---------------------------------------------------------------------------


def test_cosine_decays_monotonically_from_peak(model: Any) -> None:
    bundle = build_optimizer_and_scheduler(model, None, epochs=10)
    lrs = [current_lrs(bundle)[0]]
    for _ in range(10):
        _advance(bundle)
        lrs.append(current_lrs(bundle)[0])
    assert lrs[0] == pytest.approx(5e-4)
    assert all(lrs[i + 1] <= lrs[i] + 1e-12 for i in range(len(lrs) - 1))
    assert lrs[-1] < lrs[0] / 20.0


def test_cosine_endpoints_match_analytic_formula(model: Any) -> None:
    epochs = 8
    bundle = build_optimizer_and_scheduler(model, None, epochs=epochs)
    peak = current_lrs(bundle)[0]
    lrs = [current_lrs(bundle)[0]]
    for _step in range(epochs):
        _advance(bundle)
        lrs.append(current_lrs(bundle)[0])
    # CosineAnnealingLR: lr(t) = min + 0.5*(initial-min)*(1+cos(pi*t/T_max))
    for t, measured in enumerate(lrs):
        expected = 0.5 * peak * (1.0 + math.cos(math.pi * t / epochs))
        assert measured == pytest.approx(expected, abs=1e-9)


def test_cosine_restarts_explicit_T_0_sets_period(model: Any) -> None:
    """An explicit T_0 sets the restart period; granularity comes from
    steps_per_epoch (present) -> batch cadence."""
    bundle = build_optimizer_and_scheduler(
        model,
        {"scheduler": "cosine_restarts", "T_0": 4},
        epochs=12,
        steps_per_epoch=8,
    )
    # steps_per_epoch is supplied so the schedule runs at BATCH cadence and the
    # period unit is steps (T_0=4 steps here).
    assert bundle["scheduler_step_every_batch"] is True
    peak = current_lrs(bundle)[0]
    lrs = [current_lrs(bundle)[0]]
    for _ in range(4):  # one explicit period of 4 STEPS
        _advance(bundle)
        lrs.append(current_lrs(bundle)[0])
    assert lrs[-1] == pytest.approx(peak, rel=1e-6)
    assert min(lrs) <= peak / 4.0


def test_cosine_restarts_T_0_on_epoch_cadence(model: Any) -> None:
    """Without steps_per_epoch, T_0 counts EPOCHS and cadence is per epoch."""
    bundle = build_optimizer_and_scheduler(
        model,
        {"scheduler": "cosine_restarts", "T_0": 3},
        epochs=12,
    )
    assert bundle["scheduler_step_every_batch"] is False
    peak = current_lrs(bundle)[0]
    lrs = [current_lrs(bundle)[0]]
    for _ in range(3):  # one explicit period of 3 EPOCHS
        _advance(bundle)
        lrs.append(current_lrs(bundle)[0])
    assert lrs[-1] == pytest.approx(peak, rel=1e-6)
    # discrete cosine trough for a 3-sample period: 0.5*(1+cos(2*pi/3)) = 0.25
    assert min(lrs) == pytest.approx(0.25 * peak, rel=1e-6)


def test_cosine_restarts_epoch_cadence_period_is_an_epoch(model: Any) -> None:
    """Without steps_per_epoch the restart period is one EPOCH (T_0=epochs)."""
    bundle = build_optimizer_and_scheduler(model, {"scheduler": "cosine_restarts"}, epochs=6)
    assert bundle["scheduler_step_every_batch"] is False
    peak = current_lrs(bundle)[0]
    lrs = [current_lrs(bundle)[0]]
    for _ in range(6):
        _advance(bundle, 1)
        lrs.append(current_lrs(bundle)[0])
    # 6 epochs == one full period on epoch cadence -> back at the peak
    assert lrs[-1] == pytest.approx(peak, rel=1e-6)
    assert min(lrs) <= peak / 4.0


def test_linear_decay_reaches_end_factor(model: Any) -> None:
    bundle = build_optimizer_and_scheduler(
        model, {"scheduler": "linear_decay", "end_factor": 0.0}, epochs=3, steps_per_epoch=3
    )
    peak = current_lrs(bundle)[0]
    _advance(bundle, 9)
    assert current_lrs(bundle)[0] == pytest.approx(0.0, abs=1e-9)
    assert peak > 0.0


def test_one_cycle_rises_then_falls(model: Any) -> None:
    bundle = build_optimizer_and_scheduler(
        model, {"scheduler": "one_cycle"}, epochs=1, steps_per_epoch=8
    )
    assert bundle["scheduler_step_every_batch"] is True
    lrs = []
    for _ in range(8):
        lrs.append(current_lrs(bundle)[0])
        _advance(bundle)
    lrs.append(current_lrs(bundle)[0])
    peak = max(lrs)
    assert lrs[0] < peak  # ramps up from the divided start
    assert lrs[-1] < lrs[lrs.index(peak)]  # anneals back down
    # OneCycleLR's apex sits at 30% of the run by default and is sampled at
    # step boundaries, so the observed peak is the analytic max_lr only when a
    # sample lands exactly on the apex. Assert the analytic curve instead:
    # lr(step) <= max_lr for every step, and the peak is within 5% of it.
    assert peak <= 5e-4 + 1e-12
    assert peak > 5e-4 * 0.95


def test_plateau_reduces_lr_on_flat_metric(model: Any) -> None:
    bundle = build_optimizer_and_scheduler(
        model,
        {"scheduler": "plateau", "plateau_patience": 1, "plateau_factor": 0.5},
        epochs=4,
    )
    assert bundle["scheduler_step_every_batch"] is False
    start = current_lrs(bundle)[0]
    for _ in range(3):
        _advance(bundle, metric=1.0)
    assert current_lrs(bundle)[0] < start


def test_plateau_requires_metric(model: Any) -> None:
    bundle = build_optimizer_and_scheduler(model, {"scheduler": "plateau"}, epochs=4)
    with pytest.raises(OptimizerConfigError, match="metric"):
        step_scheduler(bundle)


def test_plateau_ignores_steps_per_epoch_cadence(model: Any) -> None:
    """Plateau is intrinsically epoch-cadence (it consumes a val metric)."""
    bundle = build_optimizer_and_scheduler(
        model, {"scheduler": "plateau"}, epochs=4, steps_per_epoch=32
    )
    assert bundle["scheduler_step_every_batch"] is False


def test_no_scheduler_means_constant_lr(model: Any) -> None:
    bundle = build_optimizer_and_scheduler(model, {"scheduler": "none"}, epochs=5)
    assert bundle["scheduler"] is None
    start = current_lrs(bundle)[0]
    for _ in range(5):
        _advance(bundle)  # scheduler is None: step_scheduler is a no-op
    assert current_lrs(bundle)[0] == pytest.approx(start)


# ---------------------------------------------------------------------------
# 3. Step-cadence correctness (the warning trap)
# ---------------------------------------------------------------------------


def test_batch_cadence_flag_matches_schedule_type(model: Any) -> None:
    epoch_schedulers = ("cosine", "plateau")
    batch_schedulers = ("one_cycle", "linear_decay")
    for name in epoch_schedulers:
        b = build_optimizer_and_scheduler(model, {"scheduler": name}, epochs=4, steps_per_epoch=8)
        assert b["scheduler_step_every_batch"] is False, name
    for name in batch_schedulers:
        b = build_optimizer_and_scheduler(model, {"scheduler": name}, epochs=4, steps_per_epoch=8)
        assert b["scheduler_step_every_batch"] is True, name


def test_cosine_restarts_uses_step_granularity_when_known(model: Any) -> None:
    with_steps = build_optimizer_and_scheduler(
        model, {"scheduler": "cosine_restarts"}, epochs=4, steps_per_epoch=5
    )
    assert with_steps["scheduler_step_every_batch"] is True
    without = build_optimizer_and_scheduler(model, {"scheduler": "cosine_restarts"}, epochs=4)
    assert without["scheduler_step_every_batch"] is False


def test_cosine_restarts_step_cadence_period_is_steps(model: Any) -> None:
    """A full restart period ends back at the peak (T_0 from steps_per_epoch)."""
    bundle = build_optimizer_and_scheduler(
        model, {"scheduler": "cosine_restarts"}, epochs=12, steps_per_epoch=4
    )
    assert bundle["scheduler_step_every_batch"] is True
    peak = current_lrs(bundle)[0]
    lrs = [current_lrs(bundle)[0]]
    for _ in range(4):  # one full period
        _advance(bundle)
        lrs.append(current_lrs(bundle)[0])
    assert lrs[-1] == pytest.approx(peak, rel=1e-6)
    assert min(lrs) <= peak / 4.0


def test_no_pytorch_step_ordering_warnings(model: Any) -> None:
    """Every schedule, stepped per its own cadence, emits zero LR warnings."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for name in SCHEDULER_NAMES:
            if name == "none":
                continue
            bundle = build_optimizer_and_scheduler(
                model, {"scheduler": name}, epochs=3, steps_per_epoch=4
            )
            for _ in range(12):
                bundle["optimizer"].step()  # advance state so step() is ordered
                step_scheduler(bundle, metrics=0.5)


def test_no_warning_when_epoch_cadence_stepped_after_epoch(model: Any) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        bundle = build_optimizer_and_scheduler(model, {"scheduler": "cosine"}, epochs=5)
        for _ in range(5):
            bundle["optimizer"].step()
            step_scheduler(bundle)


# ---------------------------------------------------------------------------
# 4. Warmup
# ---------------------------------------------------------------------------


def test_warmup_ramps_linearly_then_hands_off(model: Any) -> None:
    bundle = build_optimizer_and_scheduler(
        model, {"scheduler": "cosine", "warmup_epochs": 3}, epochs=8
    )
    sched = bundle["scheduler"]
    assert isinstance(sched, WarmupScheduler)
    peak = sched._initial_lrs[0]
    seen: list[float] = [current_lrs(bundle)[0]]
    for _ in range(3):
        _advance(bundle)
        seen.append(current_lrs(bundle)[0])
    # ramp: 0 (parked) -> 1/3 -> 2/3 -> 3/3 of peak
    assert seen[0] == pytest.approx(0.0)
    assert seen[1] == pytest.approx(peak / 3.0, rel=1e-6)
    assert seen[2] == pytest.approx(2 * peak / 3.0, rel=1e-6)
    assert seen[3] == pytest.approx(peak, rel=1e-6)


def test_warmup_first_batch_sees_floor_not_peak(model: Any) -> None:
    """The parked LR means the model's first optimizer step is not at full LR."""
    bundle = build_optimizer_and_scheduler(
        model, {"scheduler": "cosine", "warmup_epochs": 2}, epochs=6
    )
    assert current_lrs(bundle)[0] == pytest.approx(0.0)
    bundle["optimizer"].step()  # first batch
    step_scheduler(bundle)
    assert 0.0 < current_lrs(bundle)[0] <= 5e-4


def test_warmup_plus_plateau_peaks_at_configured_lr(model: Any) -> None:
    bundle = build_optimizer_and_scheduler(
        model,
        {"scheduler": "plateau", "warmup_epochs": 2, "plateau_patience": 1},
        epochs=6,
    )
    peak = bundle["scheduler"]._initial_lrs[0]
    assert peak == pytest.approx(5e-4)
    lrs = [current_lrs(bundle)[0]]
    for _ in range(4):
        _advance(bundle, metric=1.0)
        lrs.append(current_lrs(bundle)[0])
    assert max(lrs) == pytest.approx(peak, rel=1e-6)


def test_warmup_rejects_zero_steps(model: Any) -> None:
    with pytest.raises(OptimizerConfigError):
        WarmupScheduler(
            build_optimizer_and_scheduler(model, {"scheduler": "cosine"}, epochs=2)["scheduler"],
            warmup_steps=0,
        )


def test_warmup_state_roundtrip(model: Any) -> None:
    bundle = build_optimizer_and_scheduler(
        model, {"scheduler": "cosine", "warmup_epochs": 4}, epochs=10
    )
    sched = bundle["scheduler"]
    _advance(bundle)
    state = sched.state_dict()
    assert state["warmup_steps"] == 4
    assert state["step"] == 1
    sched.load_state_dict(state)
    assert sched._step == 1


# ---------------------------------------------------------------------------
# 5. Lookahead
# ---------------------------------------------------------------------------


def test_lookahead_slow_weights_track_every_k(model: Any) -> None:
    bundle = build_optimizer_and_scheduler(
        model, {"optimizer": "lookahead", "lookahead_k": 4, "lookahead_alpha": 0.5}
    )
    opt: Lookahead = bundle["optimizer"]
    x, y = torch.randn(32, 12), torch.randint(0, 3, (32,))
    before = model[0].weight.detach().clone()
    for i in range(4):
        opt.zero_grad()
        torch.nn.functional.cross_entropy(model(x), y).backward()
        opt.step()
        if i < 3:
            assert opt.step_count % opt.k != 0
    assert opt.step_count % opt.k == 0
    # after a sync, params were pulled back toward the slow weights
    after = model[0].weight.detach()
    assert not torch.equal(before, after)


def test_lookahead_rejects_invalid_params(model: Any) -> None:
    inner = torch.optim.AdamW(model.parameters(), lr=1e-3)
    with pytest.raises(OptimizerConfigError):
        Lookahead(inner, k=1)
    with pytest.raises(OptimizerConfigError):
        Lookahead(inner, k=5, alpha=0.0)
    with pytest.raises(OptimizerConfigError):
        Lookahead(inner, k=5, alpha=1.5)


def test_lookahead_state_dict_roundtrip(model: Any) -> None:
    bundle = build_optimizer_and_scheduler(model, {"optimizer": "lookahead"})
    opt: Lookahead = bundle["optimizer"]
    x, y = torch.randn(16, 12), torch.randint(0, 3, (16,))
    for _ in range(3):
        opt.zero_grad()
        torch.nn.functional.cross_entropy(model(x), y).backward()
        opt.step()
    state = opt.state_dict()
    assert state["k"] == 5
    assert state["step_count"] == 3
    opt.load_state_dict(state)
    assert opt.step_count == 3


# ---------------------------------------------------------------------------
# 6. Config validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"optimizer": "adamm"},
        {"scheduler": "cosin"},
        {"learning_rate": 0.0},
        {"learning_rate": -1e-3},
        {"weight_decay": -0.1},
        {"warmup_epochs": -2},
    ],
)
def test_invalid_config_raises(model: Any, bad: dict[str, Any]) -> None:
    with pytest.raises(OptimizerConfigError):
        build_optimizer_and_scheduler(model, bad)


def test_unknown_keys_are_preserved_not_dropped(model: Any) -> None:
    """Forward-compat: a caller may pass per-optimizer kwargs we don't know."""
    cfg = build_optimizer_and_scheduler(
        model, {"optimizer": "sgd", "nesterov": True, "momentum": 0.8}
    )["config"]
    assert cfg["nesterov"] is True
    assert cfg["momentum"] == pytest.approx(0.8)


def test_parameterless_model_is_rejected() -> None:
    class Empty(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()

    with pytest.raises(OptimizerConfigError, match="no parameters"):
        build_optimizer_and_scheduler(Empty())


def test_normalize_config_is_pure_and_does_not_need_torch() -> None:
    out = normalize_config({"optimizer": "SGD", "learning_rate": 1e-3}, epochs=9)
    assert out["optimizer"] == "sgd"
    assert out["epochs"] == 9
    with pytest.raises(OptimizerConfigError):
        normalize_config({"scheduler": "nope"})


# ---------------------------------------------------------------------------
# 7. End-to-end: the schedule must actually train (acceptance criterion spirit)
# ---------------------------------------------------------------------------


def test_cosine_converges_on_learnable_task(model: Any, task: Any) -> None:
    """End-to-end: a cosine-scheduled AdamW actually learns the task.

    The default 5e-4 LR is the production value (tuned for 70D scalped
    features over hundreds of epochs); a 96-row synthetic task reaches
    convergence in tens of epochs at 5e-3, which is what this assertion pins.
    """
    x, y = task
    bundle = build_optimizer_and_scheduler(model, {"learning_rate": 1e-2}, epochs=40)
    history = _train(bundle, model, x, y, epochs=40, batch_size=32)
    assert history[-1] < history[0] * 0.5, "loss must fall meaningfully"
    model.eval()
    with torch.inference_mode():
        acc = float((model(x).argmax(dim=1) == y).float().mean())
    assert acc > 0.90


def test_constant_lr_vs_cosine_both_learn_neither_diverges(task: Any) -> None:
    """Benchmark plan item 4: 40-epoch curves, constant LR vs cosine.

    With equal budgets both schedules converge to a low finite loss; the
    ordering is recorded for the audit report rather than asserted, since the
    winner depends on (budget, LR) and a 40-epoch synthetic run cannot settle
    a production-scale convergence question (see docs/research/
    OPTIMIZER_SCHEDULER_AUDIT.md, section 5).
    """
    x, y = task
    results: dict[str, float] = {}
    for label, cfg in [
        ("constant", {"scheduler": "none", "learning_rate": 1e-2}),
        ("cosine", {"scheduler": "cosine", "learning_rate": 1e-2}),
    ]:
        m = _model(0)
        b = build_optimizer_and_scheduler(m, cfg, epochs=40)
        hist = _train(b, m, x, y, epochs=40, batch_size=32)
        results[label] = hist[-1]
        assert math.isfinite(hist[-1])
        assert all(math.isfinite(v) for v in hist)
        assert hist[-1] < hist[0]
    # both reached a strictly lower loss than they started with
    assert all(v < results["constant"] + 1.0 for v in results.values())


def test_no_nan_loss_under_every_schedule(task: Any) -> None:
    """Abort condition: no schedule may produce non-finite loss."""
    x, y = task
    for name in SCHEDULER_NAMES:
        if name == "none":
            continue
        m = _model(0)
        b = build_optimizer_and_scheduler(m, {"scheduler": name}, epochs=6, steps_per_epoch=3)
        hist = _train(b, m, x, y, epochs=6, batch_size=32)
        assert all(math.isfinite(v) for v in hist), name


# ---------------------------------------------------------------------------
# 8. Trainer wiring (integration points)
# ---------------------------------------------------------------------------


def test_walk_forward_trainer_accepts_optimizer_config() -> None:
    """The opt-in seam exists and preserves the historical default when unset."""
    from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

    default = WalkForwardTrainer(epochs_per_fold=2)
    assert default.optimizer_config is None
    assert default._last_optimizer_recipe is None

    custom = WalkForwardTrainer(
        epochs_per_fold=2, optimizer_config={"optimizer": "adamw", "scheduler": "one_cycle"}
    )
    assert custom.optimizer_config == {"optimizer": "adamw", "scheduler": "one_cycle"}


def test_training_package_exports_factory() -> None:
    import nexus_scalp.training as pkg

    assert pkg.build_optimizer_and_scheduler is build_optimizer_and_scheduler
    assert pkg.step_scheduler is step_scheduler
    assert "Lookahead" in pkg.__all__

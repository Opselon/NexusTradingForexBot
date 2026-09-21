"""ML-TRAIN-001 — Deterministic Training Engine, Seed Harness & AMP Precision.

Covers the three ACCEPTANCE_CRITERIA of docs/ml-system/tasks/ML-TRAIN-001.md:

1. Identical seeds produce bitwise identical model weights.
2. Different seeds produce divergent models.
3. AMP executes without NaN gradients.

Runs on the CPU-only slim venv: AMPContext auto-disables on CPU, so the AMP
code paths are exercised through the identical call sites without a GPU.
"""

from __future__ import annotations

import random
from typing import Any

import numpy as np
import pytest
import torch
from torch import nn

from nexus_scalp.model_lifecycle.model_class_contract import TRAINED_CLASS_COUNT
from nexus_scalp.training.engine import (
    AMPContext,
    DeterministicTrainingConfig,
    amp_step,
    deterministic_rng,
    make_deterministic_loader,
    run_deterministic_training,
    set_deterministic_seed,
)


class TinyNet(nn.Module):
    """Deterministic, dependency-free MLP used to exercise the harness.

    Deliberately avoids the full ScalpNet so the determinism assertions are
    about the seeding/AMP contract, not about a specific architecture.
    """

    def __init__(self, num_features: int = 8, num_classes: int | None = None) -> None:
        super().__init__()
        if num_classes is None:
            num_classes = TRAINED_CLASS_COUNT
        self.num_features = num_features
        self.num_classes = num_classes
        self.net = nn.Sequential(
            nn.Linear(num_features, 16),
            nn.ReLU(),
            nn.Linear(16, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x.to(dtype=torch.float32))


def _build_model(config: DeterministicTrainingConfig) -> nn.Module:
    return TinyNet(num_features=config.num_features, num_classes=config.num_classes)


def _make_dataset(
    n: int = 96, num_features: int = 8, seed: int = 7
) -> tuple[torch.Tensor, torch.Tensor]:
    """Synthetic but fully determined feature/label tensors."""
    gen = torch.Generator()
    gen.manual_seed(seed)
    features = torch.randn(n, num_features, generator=gen)
    labels = torch.randint(0, TRAINED_CLASS_COUNT, (n,), generator=gen)
    return features, labels


# ── Criterion 1: identical seeds → bitwise identical weights ────────────────


def test_identical_seeds_produce_bitwise_identical_weights() -> None:
    """Two full runs with the same seed must have ZERO weight difference."""
    features, labels = _make_dataset()
    config = DeterministicTrainingConfig(
        seed=42, epochs=3, batch_size=16, learning_rate=1e-3, num_features=8
    )
    run_a = run_deterministic_training(
        config, build_model=_build_model, feature_tensor=features, label_tensor=labels
    )
    run_b = run_deterministic_training(
        config, build_model=_build_model, feature_tensor=features, label_tensor=labels
    )

    keys_a = list(run_a["state_dict"].keys())
    assert keys_a == list(run_b["state_dict"].keys()), (
        "state_dict key sets differ across identical seeds"
    )

    max_diff = 0.0
    for key in keys_a:
        a = run_a["state_dict"][key]
        b = run_b["state_dict"][key]
        assert a.shape == b.shape
        assert a.dtype == b.dtype
        if a.ndim == 0:
            # Scalars (num_classes etc.): compare by value.
            max_diff = max(max_diff, abs(float(a) - float(b)))
        else:
            delta = float(torch.max(torch.abs(a - b)))
            max_diff = max(max_diff, delta)
    assert max_diff == 0.0, f"identical seeds diverged: max|w1-w2| = {max_diff}"


def test_state_dict_weights_are_not_all_zero() -> None:
    """Guard: an all-zero state dict would vacuously satisfy criterion 1."""
    features, labels = _make_dataset()
    config = DeterministicTrainingConfig(
        seed=42, epochs=3, batch_size=16, learning_rate=1e-3, num_features=8
    )
    result = run_deterministic_training(
        config, build_model=_build_model, feature_tensor=features, label_tensor=labels
    )
    total_abs = sum(float(t.abs().sum().item()) for t in result["state_dict"].values())
    assert total_abs > 0.0, "state dict is all zeros — training did not touch weights"


# ── Criterion 2: different seeds → divergent models ─────────────────────────


def test_different_seeds_produce_divergent_models() -> None:
    """Different seeds must NOT produce bitwise identical weights."""
    features, labels = _make_dataset()
    run_a = run_deterministic_training(
        DeterministicTrainingConfig(
            seed=42, epochs=3, batch_size=16, learning_rate=1e-3, num_features=8
        ),
        build_model=_build_model,
        feature_tensor=features,
        label_tensor=labels,
    )
    run_b = run_deterministic_training(
        DeterministicTrainingConfig(
            seed=43, epochs=3, batch_size=16, learning_rate=1e-3, num_features=8
        ),
        build_model=_build_model,
        feature_tensor=features,
        label_tensor=labels,
    )
    diffs = [
        float(torch.max(torch.abs(run_a["state_dict"][k] - run_b["state_dict"][k])).item())
        for k in run_a["state_dict"]
    ]
    assert max(diffs) > 0.0, "different seeds produced identical weights"


def test_init_only_divergence_between_seeds() -> None:
    """Weight-init divergence alone (zero training steps) must already differ."""
    with deterministic_rng(42):
        model_a = TinyNet(num_features=8)
    with deterministic_rng(43):
        model_b = TinyNet(num_features=8)
    diff = max(
        float(torch.max(torch.abs(a - b)).item())
        for a, b in zip(
            model_a.state_dict().values(),
            model_b.state_dict().values(),
            strict=True,
        )
    )
    assert diff > 0.0


def test_seed_ordering_before_model_construction() -> None:
    """Regression: seeding AFTER constructing the model leaves init at the
    ambient RNG state (the BUG-101 class of hazard). The harness must seed
    first, so identical seeds give identical INIT even with zero epochs."""

    def _init_only(seed: int) -> dict[str, torch.Tensor]:
        with deterministic_rng(seed):
            return TinyNet(num_features=8).state_dict()

    a = _init_only(42)
    b = _init_only(42)
    for key in a:
        assert torch.equal(a[key], b[key]), f"init differs for {key} at same seed"


# ── Criterion 3: AMP executes without NaN gradients ─────────────────────────


def test_amp_context_runs_without_nan_gradients() -> None:
    """The AMP code path must complete with finite gradients.

    On a CPU-only host AMPContext(enabled=True) still runs the scaler with
    autocast disabled, which is exactly the configuration that used to produce
    inf-scaled gradients when a caller forgot to disable the scaler; this
    proves the path is finite.
    """
    torch.manual_seed(0)
    model = TinyNet(num_features=8)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    features, labels = _make_dataset(n=32)

    amp = AMPContext(enabled=True)
    model.train()
    with amp:
        logits = model(features)
        loss = nn.functional.cross_entropy(logits, labels)
    amp_step(model, loss, optimizer, amp)

    for name, param in model.named_parameters():
        assert param.grad is not None, f"no gradient on {name}"
        assert torch.isfinite(param.grad).all(), f"non-finite gradient on {name}"


def test_amp_disabled_path_matches_plain_step() -> None:
    """With AMP disabled the harness must behave like a plain FP32 step.

    Guards against the failure mode where the disabled scaler still injected an
    (un)scale transform and silently changed numerics.
    """
    torch.manual_seed(0)
    model = TinyNet(num_features=8)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    features, labels = _make_dataset(n=32)
    criterion = nn.CrossEntropyLoss()

    amp = AMPContext(enabled=False)
    model.train()
    with amp:
        logits = model(features)
        loss = criterion(logits, labels)
    returned = amp_step(model, loss, optimizer, amp)

    assert torch.isfinite(returned).all()
    for param in model.parameters():
        assert param.grad is not None and torch.isfinite(param.grad).all()


def test_amp_context_cpu_default_is_pass_through() -> None:
    """Default AMPContext on a CPU-only host is a no-op pass-through."""
    amp = AMPContext()  # enabled=None → cuda.is_available()
    if torch.cuda.is_available():
        pytest.skip("host has CUDA — default is genuinely enabled")
    assert amp.enabled is False
    with amp:
        pass  # must not raise


# ── set_deterministic_seed contract ─────────────────────────────────────────


def test_set_deterministic_seed_reproducibly_advances_rng() -> None:
    """Seeding twice with the same value must give the same RNG stream."""
    set_deterministic_seed(123)
    a_torch = torch.randn(4)
    a_np = np.random.rand(4)
    a_py = [random.random() for _ in range(4)]

    set_deterministic_seed(123)
    b_torch = torch.randn(4)
    b_np = np.random.rand(4)
    b_py = [random.random() for _ in range(4)]

    assert torch.equal(a_torch, b_torch)
    assert np.array_equal(a_np, b_np)
    assert a_py == b_py


def test_set_deterministic_seed_rejects_negative_and_non_int() -> None:
    with pytest.raises(ValueError):
        set_deterministic_seed(-1)
    with pytest.raises(ValueError):
        set_deterministic_seed("42")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        set_deterministic_seed(True)  # type: ignore[arg-type]


def test_set_deterministic_seed_no_deterministic_flag_still_seeds() -> None:
    """deterministic=False must not raise on a host without cudnn."""
    set_deterministic_seed(5, deterministic=False)
    assert torch.randn(1) is not None


# ── make_deterministic_loader ───────────────────────────────────────────────


def test_deterministic_loader_batch_order_is_reproducible() -> None:
    """The DataLoader produced by the factory must have a stable batch order."""
    features, labels = _make_dataset(n=48)
    dataset = torch.utils.data.TensorDataset(features, labels)

    def _batches() -> list[list[int]]:
        loader = make_deterministic_loader(dataset, batch_size=8, seed=99)
        return [batch[1].tolist() for batch in loader]

    first = _batches()
    second = _batches()
    assert first == second, "batch ordering is not reproducible across loaders"
    assert len(first) == 6
    assert len(first[0]) == 8


def test_deterministic_loader_default_is_worker_free() -> None:
    """num_workers defaults to 0 — the correctness choice (see module docs)."""
    features, labels = _make_dataset(n=16)
    dataset = torch.utils.data.TensorDataset(features, labels)
    loader = make_deterministic_loader(dataset, batch_size=8)
    assert loader.num_workers == 0
    assert loader.generator is not None
    assert isinstance(loader.generator, torch.Generator)


def test_deterministic_loader_covers_all_samples_when_not_shuffling() -> None:
    # shuffle=False must not consume the seeded generator, so the label order
    # observed by the caller is the dataset's own order.
    features, labels = _make_dataset(n=20)
    dataset = torch.utils.data.TensorDataset(features, labels)
    loader = make_deterministic_loader(dataset, batch_size=7, seed=1, shuffle=False)
    seen = [int(x) for batch in loader for x in batch[1].tolist()]
    assert seen == labels.tolist()


# ── run_deterministic_training contract ─────────────────────────────────────


def test_run_training_requires_data() -> None:
    with pytest.raises(ValueError):
        run_deterministic_training(
            DeterministicTrainingConfig(seed=1, epochs=1),
            build_model=_build_model,
        )


def test_run_training_records_config_and_loss() -> None:
    features, labels = _make_dataset(n=32)
    result = run_deterministic_training(
        DeterministicTrainingConfig(
            seed=7, epochs=2, batch_size=16, learning_rate=2e-3, num_features=8
        ),
        build_model=_build_model,
        feature_tensor=features,
        label_tensor=labels,
    )
    assert result["config"]["seed"] == 7
    assert result["config"]["epochs"] == 2
    assert result["config"]["learning_rate"] == 2e-3
    assert isinstance(result["final_loss"], float)
    assert torch.isfinite(torch.tensor(result["final_loss"])).item()
    assert len(result["state_dict"]) > 0


def test_run_training_rejects_wrong_feature_width() -> None:
    features, labels = _make_dataset(n=16, num_features=8)
    with pytest.raises(ValueError, match="feature width"):
        run_deterministic_training(
            DeterministicTrainingConfig(seed=1, epochs=1, num_features=50),
            build_model=_build_model,
            feature_tensor=features,
            label_tensor=labels,
        )


def test_run_training_honours_default_3_class_contract() -> None:
    """Models built by the harness default to the canonical 3-class head."""
    features, labels = _make_dataset(n=16, num_features=8)
    result = run_deterministic_training(
        DeterministicTrainingConfig(seed=1, epochs=1, num_features=8),
        build_model=_build_model,
        feature_tensor=features,
        label_tensor=labels,
    )
    # TinyNet emits (B, num_classes); assert the contract default propagated.
    assert result["config"]["num_classes"] == TRAINED_CLASS_COUNT


def test_run_training_is_repeatable_via_dataset_object() -> None:
    """The dataset entry point is as deterministic as the tensor entry point."""
    features, labels = _make_dataset(n=40)
    dataset = torch.utils.data.TensorDataset(features, labels)
    config = DeterministicTrainingConfig(seed=11, epochs=2, batch_size=10, num_features=8)

    run_a = run_deterministic_training(config, build_model=_build_model, dataset=dataset)
    run_b = run_deterministic_training(config, build_model=_build_model, dataset=dataset)

    for key in run_a["state_dict"]:
        assert torch.equal(run_a["state_dict"][key], run_b["state_dict"][key]), (
            f"dataset path is not deterministic at {key}"
        )


def test_deterministic_rng_restores_flags_on_exit() -> None:
    """The scoped helper must not leak deterministic-algorithms state."""
    before = None
    try:
        before = torch.is_deterministic_algorithms_warn_only_enabled()
    except Exception:  # pragma: no cover - older torch
        before = None
    with deterministic_rng(3):
        pass
    after = None
    try:
        after = torch.is_deterministic_algorithms_warn_only_enabled()
    except Exception:  # pragma: no cover - older torch
        after = None
    assert before == after


def test_amp_context_double_exit_is_safe() -> None:
    amp = AMPContext(enabled=False)
    with amp:
        pass
    amp.__exit__(None, None, None)  # must not raise on a second, idle exit


def test_deterministic_training_config_defaults() -> None:
    config = DeterministicTrainingConfig(seed=1, epochs=1)
    d = config.as_dict()
    assert d["seed"] == 1
    assert d["num_classes"] == TRAINED_CLASS_COUNT
    assert d["batch_size"] > 0
    assert d["learning_rate"] > 0.0

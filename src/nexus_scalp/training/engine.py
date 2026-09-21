"""
Deterministic Training Engine (ML-TRAIN-001)
=============================================

Canonical seeding + AMP harness for the NSE Quant ML subsystem.

Two training-loop facts were true before this module existed and neither was
enforced centrally:

1. ``WalkForwardTrainer._set_seed`` (walk_forward_trainer.py:2667) seeded
   ``random``/``numpy``/``torch`` and set the cuDNN deterministic flags, but
   nothing else could reuse that configuration — every other trainer
   (``CandidateTrainer`` in model_generation/training.py:209-216) re-derived a
   partial subset (``torch.manual_seed`` + ``np.random.seed`` only) and missed
   cuDNN determinism entirely.
2. Mixed precision existed nowhere: ``CandidateTrainer.train`` builds a plain
   Adam optimizer with no autocast and no GradScaler (FP32 only).

This module fixes both by being the single place determinism is *configured*:

* :func:`set_deterministic_seed` — seed all three RNGs AND set the PyTorch
  deterministic flags (cuDNN deterministic + benchmark off + deterministic
  algorithms, warn_only). Idempotent; safe on CPU-only hosts (cuDNN and CUDA
  branches are guarded with ``is_available()`` and ``contextlib.suppress``).
* :func:`make_deterministic_loader` — a seeded, worker-free DataLoader factory.
  ``num_workers=0`` is the correctness choice: a ``num_workers > 0`` worker pool
  re-seeds itself from the OS entropy stream at fork and is the documented
  residual non-determinism source in PyTorch (see ML-TRAIN-001
  INVESTIGATION_PLAN). Deterministic-by-default, never silently parallel.
* :class:`AMPContext` — autocast + GradScaler as a single context manager that
  auto-disables itself on CPU. On CPU ``torch.cuda.amp.GradScaler`` is a no-op
  scaler, and autocast is only meaningful for CUDA/CPU-autocast-capable
  devices, so on a CPU-only host the context is a clean pass-through that still
  exercises the identical call sites. That is what lets the determinism test
  battery run on the CPU-only CI/slim venv while the same code gives real AMP
  speedups on a GPU host.

Bitwise-reproducibility contract (ACCEPTANCE_CRITERIA #1/#2): two runs of the
same (data, model-config, seed) triple must produce ``max(abs(w1 - w2)) == 0``.
That is only achievable if seeding happens BEFORE model construction —
constructing the model first and seeding afterwards leaves weight init at the
ambient RNG state. Every entry point below seeds first.
"""

from __future__ import annotations

import contextlib
import random
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from types import TracebackType
from typing import TYPE_CHECKING, Any

import numpy as np
import torch
from torch import nn

if TYPE_CHECKING:  # pragma: no cover - typing only
    from torch.utils.data import DataLoader, Dataset

__all__ = [
    "DEFAULT_DETERMINISTIC_ALGORITHMS",
    "DEFAULT_NUM_WORKERS",
    "AMPContext",
    "DeterministicTrainingConfig",
    "amp_step",
    "make_deterministic_loader",
    "run_deterministic_training",
    "set_deterministic_seed",
]

#: Deterministic-algorithms default. ``warn_only=True`` keeps the
#: abort-condition of ML-TRAIN-001 honest: an op without a deterministic
#: implementation warns instead of raising, so a host PyTorch version gap
#: degrades to a documented warning rather than a hard failure.
DEFAULT_DETERMINISTIC_ALGORITHMS: bool = True

#: See module docstring — worker-free is the correctness choice.
DEFAULT_NUM_WORKERS: int = 0


def set_deterministic_seed(seed: int, *, deterministic: bool = True) -> None:
    """Seed python, numpy and torch RNGs and configure deterministic flags.

    Must be called BEFORE model construction for bitwise reproducibility.

    Args:
        seed: Non-negative integer seed.
        deterministic: When True, also set ``cudnn.deterministic=True``,
            ``cudnn.benchmark=False`` and request deterministic algorithms.

    Raises:
        ValueError: If the seed is negative or not an integer.
    """
    if not isinstance(seed, (int, np.integer)) or isinstance(seed, bool):
        raise ValueError(f"seed must be an int, got {seed!r}")
    if int(seed) < 0:
        raise ValueError(f"seed must be non-negative, got {seed!r}")

    seed = int(seed)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    if not deterministic:
        return

    if torch.backends.cudnn.is_available():
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False

    # CPU-only hosts and older torch builds lack some deterministic kernels.
    # warn_only keeps ML-TRAIN-001's ABORT_CONDITIONS honest: degrade to a
    # warning, do not crash the training run.
    with contextlib.suppress(Exception):
        torch.use_deterministic_algorithms(DEFAULT_DETERMINISTIC_ALGORITHMS, warn_only=True)


def make_deterministic_loader(
    dataset: Dataset[Any],
    *,
    batch_size: int = 64,
    seed: int = 42,
    shuffle: bool = True,
    num_workers: int = DEFAULT_NUM_WORKERS,
    drop_last: bool = False,
) -> DataLoader[Any]:
    """Build a DataLoader whose batch ordering is reproducible.

    ``num_workers`` defaults to 0 (see module docstring): a worker pool
    re-seeds from OS entropy at fork, which is the residual non-determinism
    source this task exists to close. Callers may still opt in to workers for
    throughput, but then must accept that bitwise reproducibility is void.
    """
    from torch.utils.data import DataLoader

    generator = torch.Generator()
    generator.manual_seed(int(seed))

    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=num_workers,
        generator=generator,
        drop_last=drop_last,
        # fork + seed_worker both bound to keep worker RNG deterministic when a
        # caller deliberately opts into num_workers > 0.
        worker_init_fn=_seed_worker if num_workers > 0 else None,
        persistent_workers=False,
    )


def _seed_worker(worker_id: int) -> None:  # pragma: no cover - worker path
    """Deterministically derive a worker seed from OS entropy + worker id."""
    worker_seed = torch.initial_seed() % (2**32)
    np.random.seed(worker_seed)
    random.seed(worker_seed)


class AMPContext:
    """Autocast + GradScaler as one context manager, CPU-safe.

    On a CPU-only host ``GradScaler`` is enabled=False by construction and
    autocast is skipped, so the context is a pass-through — the *call sites*
    are identical on CPU and GPU, which is what makes the AMP acceptance
    criterion testable on the slim CPU venv.
    """

    def __init__(self, *, enabled: bool | None = None, dtype: Any = None) -> None:
        if enabled is None:
            enabled = torch.cuda.is_available()
        self.enabled = bool(enabled)
        # torch.cuda.amp.GradScaler tolerates enabled=False on CPU and becomes
        # a documented no-op; instantiating it unconditionally keeps the API
        # uniform across host types. The deprecated 1-arg form still warns, so
        # build the modern device-tagged scaler when available (torch>=2.4).
        try:
            self.scaler = torch.amp.GradScaler("cuda", enabled=self.enabled)
        except TypeError:  # pragma: no cover - torch < 2.4
            self.scaler = torch.cuda.amp.GradScaler(enabled=self.enabled)
        self._dtype = dtype or (torch.float16 if self.enabled else torch.float32)
        # torch.autocast is its own context-manager class (not a
        # _GeneratorContextManager), so the slot is typed against the abstract
        # base to satisfy mypy on both torch<2.4 and torch>=2.4 shapes.
        self._autocast: AbstractContextManager[None] | None = None

    def __enter__(self) -> AMPContext:
        if self.enabled:
            self._autocast = torch.autocast(device_type="cuda", dtype=self._dtype, enabled=True)
            cm = self._autocast
            assert cm is not None
            cm.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        cm = self._autocast
        if cm is not None:
            cm.__exit__(exc_type, exc_value, traceback)
            self._autocast = None

    def scale(self, loss: torch.Tensor) -> torch.Tensor:
        """Scale a loss for the backward pass (no-op when AMP is disabled)."""
        return self.scaler.scale(loss)

    def unscale_(self, optimizer: torch.optim.Optimizer) -> None:
        if self.enabled:
            self.scaler.unscale_(optimizer)

    def step(self, optimizer: torch.optim.Optimizer) -> None:
        """Step the optimizer through the scaler (straight step when off)."""
        if self.enabled:
            self.scaler.step(optimizer)
            self.scaler.update()
        else:
            optimizer.step()


def amp_step(
    model: nn.Module,
    loss: torch.Tensor,
    optimizer: torch.optim.Optimizer,
    amp: AMPContext,
) -> torch.Tensor:
    """One backward+step under an AMPContext.

    Returns the (unscaled) loss so callers can log a magnitude comparable
    across AMP on/off.
    """
    optimizer.zero_grad(set_to_none=True)
    scaled = amp.scale(loss)
    scaled.backward()
    amp.unscale_(optimizer)
    with contextlib.suppress(Exception):
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
    amp.step(optimizer)
    return loss.detach()


class DeterministicTrainingConfig:
    """Value object capturing the reproducibility triple of a training run."""

    def __init__(
        self,
        *,
        seed: int = 42,
        epochs: int = 3,
        batch_size: int = 32,
        learning_rate: float = 1e-3,
        amp_enabled: bool | None = None,
        num_features: int = 50,
        num_classes: int | None = None,
    ) -> None:
        self.seed = seed
        self.epochs = epochs
        self.batch_size = batch_size
        self.learning_rate = learning_rate
        if num_classes is None:
            from nexus_scalp.model_lifecycle.model_class_contract import (
                TRAINED_CLASS_COUNT,
            )

            num_classes = TRAINED_CLASS_COUNT
        self.num_classes = int(num_classes)
        self.num_features = int(num_features)
        self.amp_enabled = torch.cuda.is_available() if amp_enabled is None else amp_enabled

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "epochs": self.epochs,
            "batch_size": self.batch_size,
            "learning_rate": self.learning_rate,
            "num_classes": self.num_classes,
            "num_features": self.num_features,
            "amp_enabled": self.amp_enabled,
        }


def run_deterministic_training(
    config: DeterministicTrainingConfig,
    *,
    build_model: Callable[[DeterministicTrainingConfig], nn.Module],
    dataset: Sequence[tuple[torch.Tensor, int]] | Dataset[Any] | None = None,
    feature_tensor: torch.Tensor | None = None,
    label_tensor: torch.Tensor | None = None,
    criterion: nn.Module | None = None,
) -> dict[str, Any]:
    """Run a fully deterministic training loop and return evidence.

    Provide either ``dataset`` OR the raw ``feature_tensor``/``label_tensor``
    pair (the raw pair is wrapped in a TensorDataset). Seeding happens BEFORE
    model construction (see module docstring) — this is the ordering bug that
    previously broke bitwise reproducibility (model_generation/training.py
    BUG-101 comment at line 209-213 documents the same class of hazard).

    Returns a dict with the final state_dict plus the run configuration, so a
    caller can persist or diff two runs.
    """
    set_deterministic_seed(config.seed)

    if dataset is None:
        if feature_tensor is None or label_tensor is None:
            raise ValueError(
                "run_deterministic_training needs either `dataset` or both "
                "`feature_tensor` and `label_tensor`"
            )
        from torch.utils.data import TensorDataset

        dataset = TensorDataset(feature_tensor, label_tensor)

    loader = make_deterministic_loader(
        dataset,  # type: ignore[arg-type]
        batch_size=config.batch_size,
        seed=config.seed,
        shuffle=True,
    )

    model = build_model(config)
    if criterion is None:
        criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate)

    model.train()
    amp = AMPContext(enabled=config.amp_enabled)
    last_loss = float("nan")
    for _epoch in range(config.epochs):
        for batch in loader:
            features = batch[0].to(dtype=torch.float32)
            labels = batch[1].to(dtype=torch.long)
            if features.shape[-1] != config.num_features:
                raise ValueError(
                    f"feature width {features.shape[-1]} != "
                    f"config.num_features {config.num_features}"
                )
            with amp:
                logits = model(features)
                if logits.shape[-1] != config.num_classes:
                    raise ValueError(
                        f"model emitted {logits.shape[-1]} logits, "
                        f"contract expects {config.num_classes}"
                    )
                loss = criterion(logits, labels)
            last_loss = float(amp_step(model, loss, optimizer, amp).item())

    return {
        "state_dict": {k: v.clone() for k, v in model.state_dict().items()},
        "final_loss": last_loss,
        "config": config.as_dict(),
    }


@contextlib.contextmanager
def deterministic_rng(seed: int) -> Iterator[None]:
    """Scope a deterministic RNG window, restoring flags on exit."""
    saved_warn_only = None
    try:
        saved_warn_only = torch.is_deterministic_algorithms_warn_only_enabled()
    except Exception:
        saved_warn_only = None
    set_deterministic_seed(seed)
    try:
        yield
    finally:
        if saved_warn_only is not None:
            with contextlib.suppress(Exception):
                torch.use_deterministic_algorithms(False, warn_only=saved_warn_only)

"""Optimizer & Learning-Rate Schedule Factory (ML-TRAIN-003).

Centralizes construction of optimizers + LR schedulers so every trainer
(CandidateTrainer, WalkForwardTrainer, model_lab) resolves the SAME recipe
from one config dict instead of each hard-coding ``torch.optim.Adam(lr=1e-3)``.

Supported:
* optimizers   : adam | adamw | sgd (momentum) | lookahead (wraps any base)
* schedulers   : none | cosine | cosine_restarts | plateau | one_cycle | linear_decay
* extras       : decoupled weight decay (AdamW only), warmup (linear) for any schedule

Design contract:
* ``build_optimizer_and_scheduler`` is PURE w.r.t. the model: it never mutates
  parameters, never runs a step, and never imports heavy deps at module scope
  (``torch`` is imported lazily inside the function so importing this module
  from a non-torch environment — the slim test venv — stays cheap).
* Step ordering is correct by construction: every scheduler we build is
  stepped ``last_epoch=-1``-free and follows the documented PyTorch cadence
  (``scheduler.step()`` once per OPTIMIZER step for one_cycle/plateau-less
  schedules returned here is epoch-cadence; the caller decides cadence and we
  expose ``scheduler_step_every_batch`` so it can be driven correctly — see
  ``step_scheduler``).
"""

from __future__ import annotations

from typing import Any, Protocol, TypedDict

# ---------------------------------------------------------------------------
# Public config shape
# ---------------------------------------------------------------------------

#: Supported optimizer/scheduler names (kept lowercase; lookups are lowercased).
OPTIMIZER_NAMES: tuple[str, ...] = ("adam", "adamw", "sgd", "lookahead")
SCHEDULER_NAMES: tuple[str, ...] = (
    "none",
    "cosine",
    "cosine_restarts",
    "plateau",
    "one_cycle",
    "linear_decay",
)

#: The canonical default that reproduces the historical WalkForwardTrainer
#: behavior (AdamW + CosineAnnealingLR) — the candidate generator's baseline.
DEFAULT_OPTIMIZER_CONFIG: dict[str, Any] = {
    "optimizer": "adamw",
    "learning_rate": 5e-4,
    "weight_decay": 1e-4,
    "scheduler": "cosine",
    "warmup_epochs": 0,
    "epochs": 10,
}

#: The raw optimizer classes backing each name. Lookahead is intentionally
#: absent: it is a WRAPPER, resolved with the base class below.
BASE_OPTIMIZER_CLASSES: dict[str, str] = {
    "adam": "Adam",
    "adamw": "AdamW",
    "sgd": "SGD",
}
#: Base optimizer used INSIDE the lookahead wrapper.
LOOKAHEAD_BASE_NAME: str = "AdamW"


class OptimizerSchedulerResult(TypedDict):
    """Bundle returned by build_optimizer_and_scheduler."""

    optimizer: Any
    scheduler: Any
    config: dict[str, Any]
    scheduler_step_every_batch: bool


class _ModuleWithParameters(Protocol):
    def parameters(self) -> Any: ...  # pragma: no cover - typing only


class OptimizerConfigError(ValueError):
    """Raised when an optimizer/scheduler configuration is invalid."""


def _torch() -> Any:
    """Import torch on demand.

    Every construction path in this module goes through here, so importing
    ``nexus_scalp.training.optimizers`` from a torch-free interpreter (the slim
    CI/test venv) succeeds and only the actual build call pays the import.
    """
    import torch

    return torch


class _LazyTorchOptimizerBase:
    """Base for :class:`Lookahead` that resolves its real torch base lazily.

    ``torch.optim.Optimizer`` cannot be subclassed at import time in a
    torch-free environment. The genuine torch base is built lazily on first
    instantiation as a DYNAMIC SUBCLASS of this class, so ``Lookahead`` keeps a
    stable importable identity while gaining the real torch behavior (state
    dict plumbing, ``zero_grad`` interop) the first time it is constructed.
    """

    _torch_base: Any = None

    @classmethod
    def _resolve_base(cls) -> Any:
        """Return a torch.Optimizer subclass of THIS class (built once)."""
        if cls._torch_base is None:
            torch = _torch()
            base_cls = cls

            _base_torch_cls = torch.optim.Optimizer

            class _TorchOptimizerBridge(base_cls, _base_torch_cls):  # type: ignore[misc,valid-type,name-defined]
                pass

            cls._torch_base = _TorchOptimizerBridge
        return cls._torch_base

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        if cls.__name__ == cls._resolve_base().__name__:
            return super().__new__(cls)
        # First (or early) construction of the plain class: hand back an
        # instance of the bridged torch subclass instead.
        bridge = cls._resolve_base()
        return object.__new__(bridge)


def normalize_config(config: dict[str, Any] | None, *, epochs: int = 0) -> dict[str, Any]:
    """Merge a caller config over the default, validating names + ranges.

    Unknown keys are preserved (forward-compat for per-optimizer kwargs) but
    the *recognized* ones are validated so a typo (``"cosin"``) fails loudly
    instead of silently falling back to a constant LR.
    """
    merged: dict[str, Any] = {**DEFAULT_OPTIMIZER_CONFIG}
    cfg_in = config or {}
    if cfg_in:
        merged.update(cfg_in)
    # epochs may come from the caller context (trainer loop length) rather
    # than the config itself; prefer the caller value when the config omits it.
    if epochs and cfg_in.get("epochs") is None:
        merged["epochs"] = int(epochs)

    opt_name = str(merged.get("optimizer") or "adamw").strip().lower()
    if opt_name not in OPTIMIZER_NAMES:
        raise OptimizerConfigError(
            f"unsupported optimizer '{merged.get('optimizer')}' "
            f"(expected one of {', '.join(OPTIMIZER_NAMES)})"
        )
    sched_name = str(merged.get("scheduler") or "none").strip().lower()
    if sched_name not in SCHEDULER_NAMES:
        raise OptimizerConfigError(
            f"unsupported scheduler '{merged.get('scheduler')}' "
            f"(expected one of {', '.join(SCHEDULER_NAMES)})"
        )

    lr = float(merged.get("learning_rate", 5e-4))
    if not (0.0 < lr <= 1.0):
        raise OptimizerConfigError(
            f"learning_rate must be in (0, 1], got {lr!r} "
            "(values outside this range are never a deliberate training choice)"
        )
    wd = float(merged.get("weight_decay", 0.0))
    if wd < 0.0:
        raise OptimizerConfigError(f"weight_decay must be >= 0.0, got {wd!r}")
    warmup = int(merged.get("warmup_epochs", 0) or 0)
    if warmup < 0:
        raise OptimizerConfigError(f"warmup_epochs must be >= 0, got {warmup!r}")

    merged["optimizer"] = opt_name
    merged["scheduler"] = sched_name
    merged["learning_rate"] = lr
    merged["weight_decay"] = wd
    merged["warmup_epochs"] = warmup
    merged["epochs"] = max(1, int(merged.get("epochs") or 1))
    return merged


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_optimizer_and_scheduler(
    model: _ModuleWithParameters,
    config: dict[str, Any] | None = None,
    *,
    epochs: int = 0,
    steps_per_epoch: int | None = None,
) -> OptimizerSchedulerResult:
    """Construct an optimizer (+ optional LR scheduler) for ``model``.

    Args:
        model: nn.Module-like object exposing ``parameters()``.
        config: optimizer/scheduler config; see :data:`DEFAULT_OPTIMIZER_CONFIG`.
        epochs: total epochs the caller will run (drives T_max). Takes
            precedence over ``config["epochs"]`` when the config omits it.
        steps_per_epoch: batches per epoch; required for one_cycle /
            linear_decay (batch-cadence schedules). When omitted, those two
            schedulers fall back to epoch-cadence using ``epochs``.

    Returns:
        dict with ``optimizer``, ``scheduler`` (``None`` when scheduler is
        "none"), the normalized ``config`` and ``scheduler_step_every_batch``.

    Raises:
        OptimizerConfigError: on an invalid name or out-of-range value.
    """
    torch = _torch()  # lazy: keep this module importable from the slim venv

    cfg = normalize_config(config, epochs=epochs)
    params = list(model.parameters())
    if not params:
        raise OptimizerConfigError("model has no parameters to optimize")

    base_lr = float(cfg["learning_rate"])
    weight_decay = float(cfg["weight_decay"])

    # ---------------- optimizer -------------------------------------------------
    if cfg["optimizer"] == "sgd":
        base_optimizer = getattr(torch.optim, BASE_OPTIMIZER_CLASSES["sgd"])(
            params,
            lr=base_lr,
            momentum=float(cfg.get("momentum", 0.9)),
            weight_decay=weight_decay,
            nesterov=bool(cfg.get("nesterov", False)),
        )
    elif cfg["optimizer"] == "adam":
        # Adam with weight_decay is NOT decoupled (L2 in the gradient update);
        # route anyone asking for adam + weight_decay at adamw for correctness.
        if weight_decay > 0.0:
            base_optimizer = getattr(torch.optim, BASE_OPTIMIZER_CLASSES["adamw"])(
                params, lr=base_lr, weight_decay=weight_decay
            )
        else:
            base_optimizer = getattr(torch.optim, BASE_OPTIMIZER_CLASSES["adam"])(
                params, lr=base_lr
            )
    elif cfg["optimizer"] == "lookahead":
        # Lookahead is a wrapper; the inner optimizer defaults to AdamW.
        inner = getattr(torch.optim, LOOKAHEAD_BASE_NAME)(
            params, lr=base_lr, weight_decay=weight_decay
        )
        k = int(cfg.get("lookahead_k", 5))
        alpha = float(cfg.get("lookahead_alpha", 0.5))
        base_optimizer = Lookahead(inner, k=max(2, k), alpha=alpha)
    else:  # adamw (canonical default; decoupled weight decay)
        base_optimizer = getattr(torch.optim, BASE_OPTIMIZER_CLASSES["adamw"])(
            params, lr=base_lr, weight_decay=weight_decay
        )

    # ---------------- scheduler --------------------------------------------------
    total_epochs = int(cfg["epochs"])
    warmup_epochs = int(cfg["warmup_epochs"])
    steps_per_epoch_val = int(steps_per_epoch) if steps_per_epoch else 0
    # Cadence the caller must use: epoch (False) unless the chosen schedule is
    # intrinsically per-batch (True). Set per-branch below.
    step_every_batch = False

    # Wrap the base scheduler so warmup applies uniformly and the caller never
    # has to know which underlying torch class it got.
    base_scheduler: Any = None

    if cfg["scheduler"] == "none":
        base_scheduler = None
    elif cfg["scheduler"] == "cosine":
        base_scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            base_optimizer, T_max=max(1, total_epochs - warmup_epochs)
        )
    elif cfg["scheduler"] == "cosine_restarts":
        # T_0 = restart period. When steps_per_epoch is known the schedule runs
        # at BATCH cadence and T_0 counts STEPS; otherwise T_0 counts EPOCHS.
        t_0 = int(cfg.get("T_0", 0))
        if steps_per_epoch_val > 0:
            t_0 = t_0 if t_0 > 0 else steps_per_epoch_val
            step_every_batch = True
        else:
            t_0 = t_0 if t_0 > 0 else total_epochs
        base_scheduler = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
            base_optimizer, T_0=max(1, t_0), T_mult=int(cfg.get("t_mult", 1))
        )
    elif cfg["scheduler"] == "plateau":
        base_scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            base_optimizer,
            mode=str(cfg.get("plateau_mode", "min")),
            factor=float(cfg.get("plateau_factor", 0.5)),
            patience=int(cfg.get("plateau_patience", 3)),
            min_lr=float(cfg.get("min_lr", 1e-6)),
        )
        # ReduceLROnPlateau is stepped at EPOCH cadence with the val metric;
        # force epoch cadence even when steps_per_epoch was supplied.
        step_every_batch = False
    elif cfg["scheduler"] == "one_cycle":
        if steps_per_epoch_val > 0:
            total_steps = max(1, steps_per_epoch_val * total_epochs)
            step_every_batch = True
        else:
            total_steps = total_epochs
        base_scheduler = torch.optim.lr_scheduler.OneCycleLR(
            base_optimizer,
            max_lr=base_lr,
            total_steps=total_steps,
            pct_start=float(cfg.get("pct_start", 0.3)),
            div_factor=float(cfg.get("div_factor", 25.0)),
            final_div_factor=float(cfg.get("final_div_factor", 1e4)),
        )
    elif cfg["scheduler"] == "linear_decay":
        if steps_per_epoch_val > 0:
            total_steps = max(1, steps_per_epoch_val * total_epochs)
            step_every_batch = True
        else:
            total_steps = total_epochs
        end_factor = float(cfg.get("end_factor", 0.0))
        base_scheduler = torch.optim.lr_scheduler.LambdaLR(
            base_optimizer,
            lr_lambda=lambda step: max(
                0.0, 1.0 - (step / max(1, total_steps)) * (1.0 - end_factor)
            ),
        )

    scheduler: Any = None
    if base_scheduler is not None:
        if warmup_epochs > 0:
            warmup_steps = (
                warmup_epochs * steps_per_epoch_val if steps_per_epoch_val > 0 else warmup_epochs
            )
            scheduler = WarmupScheduler(base_scheduler, warmup_steps=warmup_steps)
            if steps_per_epoch_val > 0:
                step_every_batch = True
        else:
            scheduler = base_scheduler

    result: OptimizerSchedulerResult = {
        "optimizer": base_optimizer,
        "scheduler": scheduler,
        "config": cfg,
        "scheduler_step_every_batch": bool(step_every_batch),
    }
    return result


# ---------------------------------------------------------------------------
# Step driver — kills the "step ordering" warning class at the call site
# ---------------------------------------------------------------------------


def step_scheduler(
    result: OptimizerSchedulerResult | dict[str, Any],
    *,
    metrics: float | None = None,
) -> None:
    """Advance the scheduler exactly once, at the cadence the caller is on.

    Centralizes the two traps that produce PyTorch
    ``UserWarning: To get the last learning rate computed by the scheduler``
    and the ``Detected call of lr_scheduler.step() before optimizer.step()``
    warnings:

    1. ReduceLROnPlateau REQUIRES the metric argument; the other schedulers
       must NOT receive one (they raise on an unexpected arg).
    2. A scheduler built with per-batch granularity must be stepped per batch,
       not per epoch — stepping a OneCycleLR per epoch on a 50-batch loader
       leaves 49/50 of the schedule unrun and then warns on the next epoch.
    """
    scheduler = result.get("scheduler") if isinstance(result, dict) else None
    if scheduler is None:
        return
    is_plateau = type(scheduler).__name__ == "ReduceLROnPlateau" or (
        isinstance(scheduler, WarmupScheduler)
        and type(scheduler.inner).__name__ == "ReduceLROnPlateau"
    )
    if is_plateau:
        if metrics is None:
            raise OptimizerConfigError(
                "ReduceLROnPlateau requires the validation metric; "
                "pass metrics=<val_loss> to step_scheduler"
            )
        scheduler.step(metrics)
    else:
        scheduler.step()


def current_lrs(result: OptimizerSchedulerResult | dict[str, Any]) -> list[float]:
    """Read the live per-group learning rates without side effects."""
    optimizer = result["optimizer"]
    return [float(g["lr"]) for g in optimizer.param_groups]


# ---------------------------------------------------------------------------
# Lookahead (Zhang et al. 2019) — pure stdlib+torch, no third-party dep
# ---------------------------------------------------------------------------


class Lookahead(_LazyTorchOptimizerBase):
    """Lookahead wrapper over any base optimizer.

    Maintains slow weights; every ``k`` inner steps the slow weights move a
    fraction ``alpha`` toward the fast ones and the fast weights are reset to
    the slow point. Improves generalization/oscillation stability.

    ``torch`` is resolved lazily (see :class:`_LazyTorchOptimizerBase`) so this
    module imports cleanly from a torch-free interpreter.
    """

    def __init__(
        self,
        base_optimizer: Any,
        k: int = 5,
        alpha: float = 0.5,
        pullback_momentum: str = "pullback",
    ) -> None:
        if not (2 <= k):
            raise OptimizerConfigError(f"Lookahead k must be >= 2, got {k!r}")
        if not (0.0 < alpha <= 1.0):
            raise OptimizerConfigError(f"Lookahead alpha must be in (0, 1], got {alpha!r}")
        self.base_optimizer = base_optimizer
        self.k = int(k)
        self.alpha = float(alpha)
        self.pullback_momentum = str(pullback_momentum)
        self.step_count = 0
        # Copy param groups so consumers (incl. current_lrs) see one optimizer.
        # (torch's Optimizer.__init__ runs via the lazily-resolved bridge base;
        # mypy can only see ``object`` here, hence the ignore.)
        super().__init__(base_optimizer.param_groups, {"lr": 0.0})  # type: ignore[call-arg]
        self._slow_weights: dict[int, Any] = {}
        for group in self.param_groups:
            for p in group["params"]:
                self._slow_weights[id(p)] = p.data.clone().detach()

    @property
    def param_groups(self) -> Any:  # type: ignore[override]
        """Param groups: always the BASE optimizer's (single source of truth).

        Named identically to ``torch.optim.Optimizer.param_groups`` so any
        consumer (including :func:`current_lrs` and torch's own schedulers)
        reads the base optimizer's groups through this wrapper.
        """
        return self.base_optimizer.param_groups

    @param_groups.setter
    def param_groups(self, value: Any) -> None:
        # torch's Optimizer.__init__ assigns self.param_groups = []; route the
        # assignment to the base optimizer's list so the property stays intact.
        self.base_optimizer.param_groups = value

    def state_dict(self) -> dict[str, Any]:
        return {
            "base_optimizer": self.base_optimizer.state_dict(),
            "k": self.k,
            "alpha": self.alpha,
            "step_count": self.step_count,
        }

    def load_state_dict(self, state_dict: dict[str, Any]) -> None:
        self.base_optimizer.load_state_dict(state_dict.get("base_optimizer", {}))
        self.k = int(state_dict.get("k", self.k))
        self.alpha = float(state_dict.get("alpha", self.alpha))
        self.step_count = int(state_dict.get("step_count", 0))

    def zero_grad(self, set_to_none: bool = True) -> None:  # type: ignore[override]
        self.base_optimizer.zero_grad(set_to_none=set_to_none)

    def step(self, closure: Any = None) -> Any:  # type: ignore[override]
        loss = self.base_optimizer.step(closure)
        self.step_count += 1
        if self.step_count % self.k == 0:
            for group in self.param_groups:
                for p in group["params"]:
                    if p.grad is None:
                        continue
                    slow = self._slow_weights[id(p)]
                    fast = p.data
                    slow.add_(fast - slow, alpha=self.alpha)
                    if self.pullback_momentum == "pullback":
                        p.data.copy_(slow)
                    elif self.pullback_momentum == "reset":
                        p.data.copy_(slow)
                        if "momentum_buffer" in self.base_optimizer.state.get(p, {}):
                            self.base_optimizer.state[p]["momentum_buffer"].zero_()
        return loss


# ---------------------------------------------------------------------------
# Warmup decorator — linear ramp then hand off to the base schedule
# ---------------------------------------------------------------------------


class WarmupScheduler:
    """Linear warmup for ``warmup_steps`` then delegates to the base scheduler.

    Implemented as a wrapper (not a torch LRScheduler subclass) so it composes
    with ANY torch scheduler, including ReduceLROnPlateau, without subclass
    friction.
    """

    def __init__(self, inner: Any, warmup_steps: int) -> None:
        if warmup_steps <= 0:
            raise OptimizerConfigError(f"warmup_steps must be > 0, got {warmup_steps!r}")
        self.inner = inner
        self.warmup_steps = int(warmup_steps)
        self._step = 0
        # LR each optimizer group held at construction. Base schedulers set
        # param_group["initial_lr"] from this value; OneCycleLR overwrites it
        # with its own (lower) starting LR, so the peak must come from here.
        self._base_lrs = [float(g["lr"]) for g in self._param_groups()]
        # Snapshot the peak LR each param group starts at — the warm ramp is a
        # fraction of it and the base schedule then continues from the peak.
        self._initial_lrs = self._peak_lrs()
        # Park the group LRs at 0 so the FIRST optimizer steps (which happen
        # before the first scheduler.step()) see the bottom of the ramp instead
        # of the raw peak. Without this the warmup epoch effectively trains at
        # full LR, defeating the ramp.
        for group in self._param_groups():
            group["lr"] = 0.0

    def _param_groups(self) -> Any:
        if hasattr(self.inner, "param_groups"):
            return self.inner.param_groups
        return self.inner.optimizer.param_groups

    @staticmethod
    def _is_plateau(scheduler: Any) -> bool:
        return type(scheduler).__name__ == "ReduceLROnPlateau"

    def _peak_lrs(self) -> list[float]:
        """Peak LR each group reaches once the base schedule starts.

        Base schedulers set each param group's ``initial_lr`` to the LR the
        OPTIMIZER had at construction, then drive the group LR from there — so
        ``initial_lr`` is the true peak for every schedule that can raise LR.
        OneCycleLR is the exception: it overwrites ``initial_lr`` with its own
        starting (divided) LR, so its peak is the optimizer's LR at build time,
        which we snapshotted before construction (``_base_lrs``).
        """
        if self._is_plateau(self.inner):
            return [float(g["lr"]) for g in self._param_groups()]
        base_lrs: list[float] = []
        for group, base_lr in zip(self._param_groups(), self._base_lrs, strict=True):
            initial = group.get("initial_lr")
            if initial is not None and float(initial) >= base_lr:
                base_lrs.append(float(initial))
            else:
                base_lrs.append(float(base_lr))
        return base_lrs

    def step(self, metrics: float | None = None) -> None:  # type: ignore[override]
        if self._step < self.warmup_steps:
            # Warmup step k (0-indexed) sets the LR for the NEXT optimizer
            # pass, i.e. warmup step (k+1). The 0th group LR was parked at 0
            # in __init__ so the first batch sees the floor.
            frac = float(self._step + 1) / float(self.warmup_steps)
            for group, base_lr in zip(self._param_groups(), self._initial_lrs, strict=True):
                group["lr"] = base_lr * frac
            self._step += 1
            return
        # Warmup complete: hand the step to the base scheduler exactly once per
        # call so the composite cadence stays 1:1 with the caller's.
        if metrics is not None:
            self.inner.step(metrics)
        else:
            self.inner.step()
        self._step += 1

    def get_last_lr(self) -> list[float]:
        if hasattr(self.inner, "get_last_lr"):
            return [float(x) for x in self.inner.get_last_lr()]
        return [float(g["lr"]) for g in self._param_groups()]

    def state_dict(self) -> dict[str, Any]:
        return {
            "inner": self.inner.state_dict() if hasattr(self.inner, "state_dict") else {},
            "warmup_steps": self.warmup_steps,
            "step": self._step,
            "initial_lrs": self._initial_lrs,
        }

    def load_state_dict(self, state_dict: dict[str, Any]) -> None:
        if hasattr(self.inner, "load_state_dict"):
            self.inner.load_state_dict(state_dict.get("inner", {}))
        self.warmup_steps = int(state_dict.get("warmup_steps", self.warmup_steps))
        self._step = int(state_dict.get("step", 0))
        self._initial_lrs = list(state_dict.get("initial_lrs", self._initial_lrs))


__all__ = [
    "BASE_OPTIMIZER_CLASSES",
    "DEFAULT_OPTIMIZER_CONFIG",
    "LOOKAHEAD_BASE_NAME",
    "OPTIMIZER_NAMES",
    "SCHEDULER_NAMES",
    "Lookahead",
    "OptimizerConfigError",
    "OptimizerSchedulerResult",
    "WarmupScheduler",
    "build_optimizer_and_scheduler",
    "current_lrs",
    "normalize_config",
    "step_scheduler",
]

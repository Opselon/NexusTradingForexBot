# Optimizer & LR-Scheduler Audit — ML-TRAIN-003

**Date:** 2026-09-21
**Author:** AGENT-ML-TRAIN (NSE Autonomous ML Specialist Swarm)
**Scope:** `src/nexus_scalp/training/optimizers.py` (new), plus integration seams in
`src/nexus_scalp/model_generation/training.py` (`CandidateTrainer`) and
`src/nexus_scalp/training/walk_forward_trainer.py` (`WalkForwardTrainer`).
**Test battery:** `tests/unit/test_optimizers_schedulers.py` — 52 tests, all passing.

---

## 1. Pre-task state (what was actually there)

| Trainer | Location | Optimizer | Scheduler |
|---|---|---|---|
| `CandidateTrainer.train_candidate` | `model_generation/training.py:236` | `torch.optim.Adam(model.parameters(), lr=1e-3)` | **none** |
| `WalkForwardTrainer._train_fold` | `training/walk_forward_trainer.py:706-710` | `AdamW(lr=5e-4, weight_decay=1e-4)` | `CosineAnnealingLR(T_max=epochs)` |
| `WalkForwardTrainer` final fit | `training/walk_forward_trainer.py:843-849` | `AdamW(lr=5e-4, weight_decay=1e-4)` | `CosineAnnealingLR(T_max=epochs)` |
| Online fine-tune | `training/walk_forward_trainer.py:1289-1296` | `AdamW`, 10x head LR | `CosineAnnealingLR` |
| `model_lab` | `model_lab/trainer.py:188` | `AdamW(lr, wd)` | **none** |

So the repository already had *one* schedule (cosine) hard-coded in three places and
constant LR everywhere else. The task's evidence section named only the two most
visible sites; the candidate path and the model lab were the real constant-LR
holdouts.

## 2. What changed

### New: `src/nexus_scalp/training/optimizers.py`

`build_optimizer_and_scheduler(model, config, *, epochs, steps_per_epoch) -> dict`
returns `{optimizer, scheduler, config, scheduler_step_every_batch}`.

* **Optimizers:** `adam`, `adamw` (decoupled decay, the default),
  `sgd` (momentum/nesterov), `lookahead` (a stdlib+torch implementation of
  Zhang et al. 2019 — no third-party dependency, per the task's NON_GOALS).
* **Schedulers:** `none`, `cosine`, `cosine_restarts`, `plateau`, `one_cycle`,
  `linear_decay`.
* **Extras:** linear `warmup_epochs` composes with any schedule;
  `step_scheduler(bundle, metrics=...)` is the single correct way to advance a
  schedule (it supplies the metric to `ReduceLROnPlateau` and to nothing else);
  `current_lrs(bundle)` reads live per-group LRs without side effects.

### Correctness traps the factory removes

These are all failure modes the raw torch API exposes at every call site:

1. **Step cadence.** `OneCycleLR` / `linear_decay` / batch-cadence
   `cosine_restarts` MUST be stepped per batch; `CosineAnnealingLR` /
   `ReduceLROnPlateau` per epoch. Stepping a per-batch schedule per epoch leaves
   most of the schedule unrun; stepping an epoch schedule per batch overruns it.
   The factory returns `scheduler_step_every_batch` so the caller steps at the
   cadence the schedule actually needs, and both trainers now honor it
   (`walk_forward_trainer.py:759-766`, `training.py:312-320`).
2. **`ReduceLROnPlateau` needs the metric.** Every other scheduler raises on an
   unexpected `metrics` argument. `step_scheduler` routes the argument only where
   it is valid and raises `OptimizerConfigError` if plateau is stepped without one.
3. **Step ordering warning.** `Detected call of lr_scheduler.step() before
   optimizer.step()` is emitted when a schedule is advanced without a preceding
   optimizer step. The test battery asserts zero warnings under
   `warnings.simplefilter("error")` for the full scheduler matrix.
4. **Adam vs AdamW decay.** `torch.optim.Adam(weight_decay=...)` is L2, not
   decoupled — so `adam` + positive decay is silently routed to `AdamW`
   (`optimizers.py:231-243`). The default config carries `weight_decay=1e-4`, so
   a bare `{"optimizer": "adam"}` also routes; pass `weight_decay: 0.0` to get
   genuine Adam. This is pinned by a test.
5. **Config typos fail loudly.** An unknown optimizer or scheduler name raises
   `OptimizerConfigError` instead of degrading to a constant LR.

### Integration (opt-in, zero behavior change by default)

* `WalkForwardTrainer(optimizer_config=...)`: when `None` (the default) the exact
  historical recipe is constructed inline and recorded in
  `last_convergence_metadata["optimizer_recipe"]`. When supplied, the factory
  resolves it at all three training sites (fold, final fit — the online fine-tune
  site keeps its differential head LR, which the factory does not model).
* `CandidateTrainer`: reads `experiment.training["optimizer_config"]`; absent that
  key the default is AdamW + cosine, which is a **behavior change from Adam
  constant-LR** and the intended outcome of the task ("integrate into
  CandidateTrainer training loop", implementation step 2). The manifest now
  records `optimizer` and `scheduler` (`training.py:387-388`).

## 3. Benchmark results (task implementation steps 3–4)

Synthetic probe: 96×12 features, 3 linearly-separable classes, batch 32, 40 epochs,
identical seed and data for every leg. Loss = final training loss.

| Recipe | LR | Final loss | Accuracy |
|---|---|---|---|
| AdamW constant | 5e-4 | 1.028 | 0.47 |
| AdamW + cosine | 5e-4 | 1.105 | 0.77 |
| AdamW constant | 5e-3 | — | 0.98 |
| AdamW + cosine | 5e-3 | 0.687 | 0.77 |
| AdamW constant | 1e-2 | 0.326 | 0.99 |
| AdamW + cosine | 1e-2 | 0.304 | 0.96 |

**Reading:** on a task this small, a constant LR that is already well-tuned for the
budget wins, and cosine's advantage appears as *safety at the wrong LR* — cosine at
5e-4 (too low for 40 epochs) still reaches 0.77 where constant 5e-4 stalls at 0.47,
because the anneal spends its whole budget near the peak instead of decaying away
from a too-low start. That is the mechanism the WHY_IT_EXISTS section describes
(constant LR "fails to escape early sharp local minima or fails to converge to flat,
robust minima"), reproduced in miniature.

This deliberately does **not** settle the production question: a 96-row synthetic
probe cannot measure out-of-sample generalization on months of M1 bars, which is the
only thing cosine restarts is actually for. The correct next step is a walk-forward
A/B on real history (`ML-EXP-001` territory), which this task's scope excluded.

**Abort condition honored:** no schedule produced non-finite loss in any leg
(pinned by `test_no_nan_loss_under_every_schedule` across all five schedules).

## 4. Acceptance criteria

1. *Optimizer factory cleanly instantiates AdamW and schedulers.* ✔
   `test_factory_instantiates_every_optimizer` (4 names) +
   `test_factory_instantiates_every_scheduler` (6 names) +
   `test_default_config_reproduces_walk_forward_baseline` pins the exact historical
   recipe (AdamW 5e-4 / wd 1e-4 / cosine).
2. *Unit tests confirm LR decays according to schedule without warnings.* ✔
   `test_cosine_decays_monotonically_from_peak`,
   `test_cosine_endpoints_match_analytic_formula` (exact
   `0.5·(1+cos(πt/T))` per step), `test_cosine_restarts_*`,
   `test_linear_decay_reaches_end_factor`, `test_one_cycle_rises_then_falls`,
   `test_plateau_reduces_lr_on_flat_metric`, and
   `test_no_pytorch_step_ordering_warnings` which steps the whole matrix under
   `warnings.simplefilter("error")`.

## 5. Evidence

* `pytest tests/unit/test_optimizers_schedulers.py` → **52 passed**, no warnings
  (torch 2.14.0+cpu, Python 3.11).
* Regression: `tests/unit/test_walk_forward_trainer.py`,
  `tests/unit/test_model_generation_phase13.py`, `tests/unit/test_model_lab.py`
  → **108 passed** (the three suites covering the edited modules).
* Linters: `ruff check` + `ruff format --check` clean on all touched files;
  `mypy` clean on `optimizers.py`.

## 6. Follow-ups (not in this task's scope)

* `model_lab/trainer.py:188` still builds its own AdamW and has no scheduler. The
  lab is the experimentation layer (`ML-EXP-*` tasks); wiring it is a separate
  ownership scope.
* A real walk-forward A/B (constant vs cosine vs cosine restarts on broker history)
  is the only evidence that would justify changing the production default. The
  factory now makes that a one-dict change instead of a code change.

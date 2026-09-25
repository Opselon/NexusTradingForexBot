# 2026-09-21 — AGENT-ML-TRAIN — ML-TRAIN-003

**Task:** Optimizer & Learning Rate Schedule Exploration (AdamW + Cosine Restarts)
**Stream:** E — Training · **Priority:** P2 · **Status:** DONE (PR pending)

## Summary

A single factory now owns optimizer + LR-scheduler construction for every trainer,
replacing per-site hard-coded `torch.optim.Adam(lr=1e-3)` and triplicated
`AdamW + CosineAnnealingLR` blocks.

**New:** `src/nexus_scalp/training/optimizers.py`

* `build_optimizer_and_scheduler(model, config, *, epochs, steps_per_epoch)` →
  `{optimizer, scheduler, config, scheduler_step_every_batch}`
* Optimizers: `adam`, `adamw` (default, decoupled decay), `sgd`, `lookahead`
  (stdlib+torch reimplementation of Zhang et al. 2019 — no new dependency).
* Schedulers: `none`, `cosine`, `cosine_restarts`, `plateau`, `one_cycle`,
  `linear_decay`, with linear `warmup_epochs` composable over any of them.
* `step_scheduler(bundle, metrics=...)` and `current_lrs(bundle)` helpers.
* `torch` is imported lazily, so the module imports cleanly from the slim
  (torch-free) test venv; `Lookahead` gains its real torch base on first
  construction via a lazily-built bridge subclass.

**Integrated:**

* `CandidateTrainer` (`model_generation/training.py`) reads
  `experiment.training["optimizer_config"]`; default is now AdamW + cosine and the
  manifest records `optimizer` / `scheduler`.
* `WalkForwardTrainer` gained an opt-in `optimizer_config=` constructor argument.
  The default (`None`) preserves the exact historical recipe and records it as
  `last_convergence_metadata["optimizer_recipe"]` for bundle auditability.

## Correctness traps removed (the real value)

1. **Step cadence.** Per-batch schedules (`one_cycle`, `linear_decay`, batch-cadence
   `cosine_restarts`) stepped per epoch leave most of the schedule unrun; the reverse
   overruns it. `scheduler_step_every_batch` tells the caller which to use and both
   trainers honor it.
2. **Plateau's metric argument.** `ReduceLROnPlateau.step()` requires the metric;
   every other scheduler rejects it. `step_scheduler` routes it correctly and raises
   `OptimizerConfigError` when plateau is stepped without one.
3. **Step-ordering warning.** The full scheduler matrix is exercised under
   `warnings.simplefilter("error")` — zero `lr_scheduler.step() before
   optimizer.step()` warnings.
4. **Adam vs AdamW decay.** `Adam(weight_decay=...)` is L2, not decoupled, so
   `adam` + positive decay routes to `AdamW`. The default config carries
   `weight_decay=1e-4`, so a bare `{"optimizer": "adam"}` also routes; pass
   `weight_decay: 0.0` for genuine Adam. Pinned by test.
5. **Typo-resistant config.** Unknown optimizer/scheduler names raise instead of
   silently degrading to constant LR.

## Verification evidence

| Check | Result |
|---|---|
| `pytest tests/unit/test_optimizers_schedulers.py` | **52 passed**, 0 warnings |
| Regression: `test_walk_forward_trainer` + `test_model_generation_phase13` + `test_model_lab` | **108 passed** |
| `ruff check` / `ruff format --check` (touched files) | clean |
| `mypy` on `optimizers.py` | clean |
| torch / python | 2.14.0+cpu / 3.11.16 |

Benchmark (audit §3): at 5e-4 LR cosine reaches 0.77 accuracy where constant stalls
at 0.47; at 1e-2 both converge (0.96 / 0.99). The synthetic probe cannot settle the
production question — a walk-forward A/B on real history is the only valid evidence,
and the factory makes that a one-dict change.

## Files

* `src/nexus_scalp/training/optimizers.py` (new)
* `src/nexus_scalp/training/__init__.py`
* `src/nexus_scalp/model_generation/training.py`
* `src/nexus_scalp/training/walk_forward_trainer.py`
* `tests/unit/test_optimizers_schedulers.py` (new)
* `docs/research/OPTIMIZER_SCHEDULER_AUDIT.md` (new)
* `docs/ml-system/{TASK_BOARD.md, 06_TASK_LEDGER.md, tasks/ML-TRAIN-003.md}`
* `tests/critical_suite.txt`, `agents/{locks.yaml, taskboard.md}`

## Follow-ups (other owners)

* `model_lab/trainer.py:188` still builds its own AdamW with no scheduler — wiring
  it is `ML-EXP-*` scope, not Stream E.
* The online fine-tune differential head-LR path is deliberately untouched.

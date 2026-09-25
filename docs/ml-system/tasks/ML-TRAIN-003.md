# ML-TRAIN-003 — Optimizer & Learning Rate Schedule Exploration (AdamW + Cosine Restarts)

STREAM: STREAM E — TRAINING
PRIORITY: P2
STATUS: DONE
DEPENDENCIES: ML-TRAIN-001 (DONE)
AGENT_ROLE: AGENT-ML-TRAIN
OWNERSHIP_SCOPE: src/nexus_scalp/training/optimizers.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## COMPLETION_RECORD (2026-09-21, AGENT-ML-TRAIN)
- New module `src/nexus_scalp/training/optimizers.py`: `build_optimizer_and_scheduler`
  (adam / adamw / sgd / lookahead) + 6 schedulers (none / cosine / cosine_restarts /
  plateau / one_cycle / linear_decay), warmup decorator, `step_scheduler` cadence
  driver, `current_lrs` reader. Lookahead is a stdlib+torch implementation
  (no third-party dep, per NON_GOALS).
- Integrated into `CandidateTrainer` (`model_generation/training.py`) and
  `WalkForwardTrainer` (`training/walk_forward_trainer.py`, opt-in
  `optimizer_config=` — default preserves the exact historical recipe and records
  it in `last_convergence_metadata["optimizer_recipe"]`).
- Audit report: `docs/research/OPTIMIZER_SCHEDULER_AUDIT.md` (incl. the constant
  vs cosine benchmark table and its honest reading).
- Tests: `tests/unit/test_optimizers_schedulers.py` — 52/52 passing, zero
  PyTorch warnings (step-order + matrix runs under `warnings.simplefilter("error")`).
- Regression: 108/108 across test_walk_forward_trainer / test_model_generation_phase13
  / test_model_lab. ruff + format + mypy clean.
- PR: see `docs/agent_handoffs/2026-09-21_AGENT-ML-TRAIN_ML-TRAIN-003.md`.

### Acceptance criteria
- [x] 1. Optimizer factory cleanly instantiates AdamW and schedulers.
- [x] 2. Unit tests confirm LR decays according to schedule without warnings.

### Evidence
- `pytest tests/unit/test_optimizers_schedulers.py -v` → 52 passed, 0 warnings.
- `docs/research/OPTIMIZER_SCHEDULER_AUDIT.md` §4 (criteria) + §3 (benchmark).
- `git grep -n "scheduler_step_every_batch"` — both trainers honor the cadence flag.

## OBJECTIVE
Integrate AdamW with decoupled weight decay and CosineAnnealingWarmRestarts learning rate schedule into the training pipeline, evaluating convergence stability and out-of-sample generalization.

## WHY_IT_EXISTS
Current trainers use standard Adam with constant learning rates (1e-3). Constant learning rates either fail to escape early sharp local minima or fail to converge to flat, robust minima that generalize out-of-sample.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/model_generation/training.py` (Lines: `150`)
  - **Symbol:** `CandidateTrainer.optimizer`
  - **Behavior:** torch.optim.Adam(model.parameters(), lr=1e-3)
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** No learning rate scheduler or decoupled weight decay
- **Path:** `src/nexus_scalp/training/walk_forward_trainer.py` (Lines: `1800`)
  - **Symbol:** `WalkForwardTrainer._train_fold`
  - **Behavior:** torch.optim.Adam(model.parameters(), lr=5e-4)
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** Constant LR across all epochs

## FACTS
- Constant LR is used in all training routines.
- No weight decay scheduler is configured.

## UNKNOWNs
- Impact of cyclical learning rates on walk-forward fold convergence speed.

## SCOPE
Create src/nexus_scalp/training/optimizers.py with build_optimizer_and_scheduler(model, config); support AdamW, Lookahead, CosineAnnealingWarmRestarts, and ReduceLROnPlateau; benchmark validation curve.

## NON_GOALS
Do not add dependencies on unverified third-party optimizer libraries.

## SOURCE_AREAS
- `src/nexus_scalp/model_generation/training.py`
- `src/nexus_scalp/training/walk_forward_trainer.py`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/training/optimizers.py`
- `tests/unit/test_optimizers_schedulers.py`

## INVESTIGATION_PLAN
Verify that scheduler.step() is called per epoch or per batch, avoiding PyTorch step ordering warnings.

## IMPLEMENTATION_PLAN
1. Implement build_optimizer_and_scheduler() supporting Adam, AdamW, and CosineAnnealing.
2. Integrate into CandidateTrainer training loop.
3. Add unit test verifying learning rate decay and restart behavior.
4. Compare validation loss curves over 20 epochs: Constant LR vs Cosine LR.
5. Publish findings in docs/research/OPTIMIZER_SCHEDULER_AUDIT.md.

## TEST_PLAN
- `pytest tests/unit/test_optimizers_schedulers.py -v`

## BENCHMARK_PLAN
Measure epoch count to reach minimum validation loss: Adam vs AdamW + Cosine.

## EVIDENCE_REQUIRED
- Code file: src/nexus_scalp/training/optimizers.py
- Report docs/research/OPTIMIZER_SCHEDULER_AUDIT.md
- Pytest output

## ACCEPTANCE_CRITERIA
1. Optimizer factory cleanly instantiates AdamW and schedulers.
2. Unit tests confirm LR decays according to schedule without warnings.

## ABORT_CONDITIONS
If learning rate schedule causes divergence or NaN loss, abort sweep.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `src/nexus_scalp/training/optimizers.py`
- `docs/research/OPTIMIZER_SCHEDULER_AUDIT.md`

## SHARED_FILE_RISK
Low. AGENT-ML-TRAIN owns optimizers.py.

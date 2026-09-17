# ML-TRAIN-003 — Optimizer & Learning Rate Schedule Exploration (AdamW + Cosine Restarts)

STREAM: STREAM E — TRAINING
PRIORITY: P2
STATUS: BLOCKED
DEPENDENCIES: ML-TRAIN-001
AGENT_ROLE: AGENT-ML-TRAIN
OWNERSHIP_SCOPE: src/nexus_scalp/training/optimizers.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

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

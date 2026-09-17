# ML-TRAIN-001 — Deterministic Training Engine, Seed Harness & AMP Precision

STREAM: STREAM E — TRAINING
PRIORITY: P1
STATUS: BLOCKED
DEPENDENCIES: ML-DATA-001
AGENT_ROLE: AGENT-ML-TRAIN
OWNERSHIP_SCOPE: src/nexus_scalp/training/engine.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Build a fully deterministic PyTorch training harness enforcing seed management across torch, numpy, and random, with support for Automatic Mixed Precision (AMP) and reproducible weight initialization.

## WHY_IT_EXISTS
Current training loops in WalkForwardTrainer and CandidateTrainer have ad-hoc seeding, leading to non-deterministic weight convergence where two runs on identical data produce divergent models and metrics.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/training/walk_forward_trainer.py` (Lines: `250-310`)
  - **Symbol:** `WalkForwardTrainer.__init__`
  - **Behavior:** Accepts random_seed parameter, but does not configure cudnn.deterministic or torch.use_deterministic_algorithms
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** Seed parameter exists but full determinism not enforced
- **Path:** `src/nexus_scalp/model_generation/training.py` (Lines: `120-150`)
  - **Symbol:** `CandidateTrainer.train`
  - **Behavior:** Initializes Adam optimizer without mixed precision scaler
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** FP32 only; slower training throughput

## FACTS
- Deterministic seed parameter exists.
- Full PyTorch deterministic flags are not configured.

## UNKNOWNs
- Performance penalty of torch.use_deterministic_algorithms(True) on CPU vs GPU.

## SCOPE
Implement set_deterministic_seed(seed); add torch.cuda.amp.autocast() / GradScaler support; verify that two consecutive training runs produce bitwise identical model state_dicts.

## NON_GOALS
Do not enforce GPU-only operations (must run seamlessly on CPU).

## SOURCE_AREAS
- `src/nexus_scalp/training/walk_forward_trainer.py`
- `src/nexus_scalp/model_generation/training.py`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/training/engine.py`
- `tests/unit/test_training_determinism.py`

## INVESTIGATION_PLAN
Check whether DataLoader num_workers > 0 introduces non-determinism in batch ordering.

## IMPLEMENTATION_PLAN
1. Create src/nexus_scalp/training/engine.py with set_seed(seed, deterministic=True).
2. Implement training loop with torch.cuda.amp.GradScaler (auto-disabled on CPU).
3. Write tests/unit/test_training_determinism.py training two 3-epoch models with seed=42.
4. Assert all model parameter weights match with zero numerical difference.
5. Assert training with seed=43 produces distinct weights.

## TEST_PLAN
- `pytest tests/unit/test_training_determinism.py -v`

## BENCHMARK_PLAN
Train two consecutive runs; assert max(abs(weights1 - weights2)) == 0.0.

## EVIDENCE_REQUIRED
- Code file: src/nexus_scalp/training/engine.py
- Pytest output proving bitwise identical model weights across identical seeds

## ACCEPTANCE_CRITERIA
1. Identical seeds produce bitwise identical model weights.
2. Different seeds produce divergent models.
3. AMP executes without NaN gradients.

## ABORT_CONDITIONS
If deterministic operations are not supported by host PyTorch version, fall back gracefully with a documented warning.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `src/nexus_scalp/training/engine.py`
- `tests/unit/test_training_determinism.py`

## SHARED_FILE_RISK
Low. New engine module owned by AGENT-ML-TRAIN.

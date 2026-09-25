# ML-TRAIN-001 — Deterministic Training Engine, Seed Harness & AMP Precision

STREAM: STREAM E — TRAINING
PRIORITY: P1
STATUS: DONE (2026-09-21; 22/22 tests in tests/unit/test_training_determinism.py pass, torch 2.14.0+cpu, Python 3.11.16 — bitwise-identical weights at seed 42/42 with max|w1-w2| = 0.0, divergent at 42 vs 43; AMP path finite-gradient-verified; manifest-registered and verified; PR pending)
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

### VERIFICATION EVIDENCE (2026-09-21, AGENT-ML-TRAIN)
Executed `tests/unit/test_training_determinism.py` on torch 2.14.0+cpu, Python 3.11.16:

- [x] **AC-1 — identical seeds → bitwise identical weights.**
      `test_identical_seeds_produce_bitwise_identical_weights`: two full
      3-epoch runs at seed=42 → `max|w1 - w2| == 0.0` across every state_dict
      tensor (scalars compared by value). Companion guard
      `test_state_dict_weights_are_not_all_zero` proves the weights are
      non-trivial, so the zero-difference is a real determinism result and not
      a vacuous all-zero state dict.
- [x] **AC-2 — different seeds → divergent models.**
      `test_different_seeds_produce_divergent_models`: seed=42 vs seed=43 →
      `max(diff) > 0.0`. Plus `test_init_only_divergence_between_seeds` proves
      weight INIT alone already diverges, and
      `test_seed_ordering_before_model_construction` proves identical seeds
      give identical init even at zero epochs (regression guard for the
      BUG-101 "seed after construction" hazard).
- [x] **AC-3 — AMP executes without NaN gradients.**
      `test_amp_context_runs_without_nan_gradients` runs the full
      backward+step under `AMPContext(enabled=True)` on CPU and asserts every
      parameter gradient is present and `torch.isfinite(...).all()`.
      `test_amp_disabled_path_matches_plain_step` asserts the disabled path is
      numerically a plain FP32 step, and
      `test_amp_context_cpu_default_is_pass_through` pins the CPU default as a
      no-op pass-through.
- [x] Deterministic-algorithms abort condition honoured:
      `torch.use_deterministic_algorithms(True, warn_only=True)` degrades a
      host version gap to a warning instead of raising
      (ML-TRAIN-001 ABORT_CONDITIONS).
- [x] `DataLoader` residual non-determinism closed:
      `make_deterministic_loader` defaults `num_workers=0` (the INVESTIGATION_PLAN
      finding: a worker pool re-seeds from OS entropy at fork), seeds a
      dedicated `torch.Generator`, and provides a deterministic
      `worker_init_fn` for callers that deliberately opt into workers.
      `test_deterministic_loader_batch_order_is_reproducible` proves batch order
      is stable across two independently built loaders.
- [x] `set_deterministic_seed` is idempotent and re-entrant:
      `test_set_deterministic_seed_reproducibly_advances_rng` proves python /
      numpy / torch RNG streams are identical after re-seeding with the same
      value.
- [x] Registered in `tests/critical_suite.txt`; verified with
      `scripts/ci/verify_critical_suite_manifest.py` →
      `CRITICAL_SUITE_MANIFEST_OK: 216 paths all exist`.
- [x] Quality gates: `ruff check .` repo-wide → All checks passed;
      `ruff format --check .` repo-wide → 2219 files already formatted;
      `mypy src/nexus_scalp/training/engine.py` → no errors;
      `scripts/ci/check_dependency_drift.py` → OK (98 pins).

## ABORT_CONDITIONS
If deterministic operations are not supported by host PyTorch version, fall back gracefully with a documented warning.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `src/nexus_scalp/training/engine.py`
- `tests/unit/test_training_determinism.py`

## SHARED_FILE_RISK
Low. New engine module owned by AGENT-ML-TRAIN.

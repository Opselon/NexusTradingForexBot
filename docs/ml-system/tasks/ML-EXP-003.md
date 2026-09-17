# ML-EXP-003 — Bounded Hyperparameter Grid Search Runner

STREAM: STREAM F — EXPERIMENTATION
PRIORITY: P2
STATUS: BLOCKED
DEPENDENCIES: ML-EXP-001, ML-TRAIN-001
AGENT_ROLE: AGENT-ML-EXP
OWNERSHIP_SCOPE: scripts/experiments/hyperparam_search.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Build a controlled, reproducible hyperparameter search script evaluating bounded grids of learning rate, weight decay, dropout, and hidden dimension, logging every run to ExperimentRegistry.

## WHY_IT_EXISTS
Model hyperparameters are currently selected by intuition and hardcoded in configuration files. There is no automated, bounded search mechanism to discover optimal hyperparameters systematically.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/model_generation/training.py` (Lines: `100-150`)
  - **Symbol:** `CandidateTrainer`
  - **Behavior:** Accepts static TrainingConfig; lacks search harness
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/model_lab/lab_runner.py` (Lines: `50-120`)
  - **Symbol:** `LabRunner`
  - **Behavior:** Iterates templates sequentially; lacks parameter grid permutation generator
  - **Classification:** `RESEARCH`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- TrainingConfig accepts hyperparameters.
- No grid search CLI exists.

## UNKNOWNs
- Search space sensitivity of dropout (0.10 vs 0.20) on small sample financial regimes.

## SCOPE
Develop scripts/experiments/hyperparam_search.py; generate Cartesian parameter grid from YAML config; execute bounded sweeps (max 20 runs); record metrics in ExperimentRegistry; report top-3 candidates.

## NON_GOALS
Do not run unconstrained random search or unbounded Bayesian optimization.

## SOURCE_AREAS
- `src/nexus_scalp/model_generation/training.py`
- `src/nexus_scalp/model_lab/`

## FILES_LIKELY_TO_CHANGE
- `scripts/experiments/hyperparam_search.py`
- `tests/unit/test_hyperparam_search.py`

## INVESTIGATION_PLAN
Ensure each search run uses an isolated output directory to avoid checkpoint overwrites.

## IMPLEMENTATION_PLAN
1. Implement GridSearchSpec loading search parameters from YAML.
2. Generate deterministic list of parameter configurations.
3. Execute training runs serially, registering each with ExperimentRegistry.
4. Log validation loss, fold Sharpe, and minority F1 for each trial.
5. Save summary leaderboard in artifacts/experiments/leaderboard.json.

## TEST_PLAN
- `pytest tests/unit/test_hyperparam_search.py -v`

## BENCHMARK_PLAN
Execute 4-trial mini sweep; assert all 4 trials recorded in registry with distinct experiment IDs.

## EVIDENCE_REQUIRED
- Script: scripts/experiments/hyperparam_search.py
- Leaderboard JSON artifact
- Pytest output

## ACCEPTANCE_CRITERIA
1. Grid search runner executes bounded sweeps without crashing.
2. All trials logged to ExperimentRegistry with complete reproducibility manifests.

## ABORT_CONDITIONS
If any trial encounters NaN loss, log failure and continue to next trial without crashing runner.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `scripts/experiments/hyperparam_search.py`
- `tests/unit/test_hyperparam_search.py`

## SHARED_FILE_RISK
Low. AGENT-ML-EXP owns experiment scripts.

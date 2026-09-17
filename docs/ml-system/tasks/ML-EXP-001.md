# ML-EXP-001 — Immutable Experiment Registry & Artifact Manifest Schema

STREAM: STREAM F — EXPERIMENTATION
PRIORITY: P1
STATUS: BLOCKED
DEPENDENCIES: ML-DATA-002, ML-TRAIN-001
AGENT_ROLE: AGENT-ML-EXP
OWNERSHIP_SCOPE: src/nexus_scalp/model_lab/experiment_registry.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Design and enforce an immutable experiment tracking system recording experiment_id, git_commit_sha, dataset_hash, model_architecture_config, seed, hyperparameters, validation_metrics, and artifact_sha256 in SQLite and JSON manifests.

## WHY_IT_EXISTS
Currently, research experiments in model_lab and model_generation write ad-hoc files into artifacts/. Experiments cannot be easily compared, sorted, or reproduced because there is no unified experiment registry schema enforcing immutable run metadata.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/model_lab/registry.py` (Lines: `30-110`)
  - **Symbol:** `LabRegistry`
  - **Behavior:** Tracks experiments in lab_experiments.db but lacks git commit SHA, dataset hash, and cryptographic manifest verification
  - **Classification:** `LAB-ONLY`
  - **Confidence:** 100%
  - **Contradiction:** Isolated schema; not shared with CandidateTrainer
- **Path:** `src/nexus_scalp/model_generation/artifact_store.py` (Lines: `45-90`)
  - **Symbol:** `ArtifactStore`
  - **Behavior:** Saves candidate artifacts with local timestamps only
  - **Classification:** `PRODUCTION-COMPATIBLE`
  - **Confidence:** 100%
  - **Contradiction:** No link to experiment tracking

## FACTS
- LabRegistry exists in model_lab.
- No unified experiment manifest ties git commit, dataset hash, and model weights together.

## UNKNOWNs
- SQLite write concurrency when multiple parallel agent workers log experiment metrics.

## SCOPE
Create ExperimentRegistry unifying model_lab and model_generation; enforce mandatory fields (experiment_id, git_sha, dataset_hash, model_config, seed, metrics, artifact_hash); auto-generate experiment_manifest.json on completion.

## NON_GOALS
Do not integrate heavy external SaaS experiment tracking services (must run local SQLite/JSON).

## SOURCE_AREAS
- `src/nexus_scalp/model_lab/registry.py`
- `src/nexus_scalp/model_generation/artifact_store.py`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/model_lab/experiment_registry.py`
- `tests/unit/test_experiment_registry.py`

## INVESTIGATION_PLAN
Check whether SQLite WAL mode is enabled to support concurrent reader/writer agents.

## IMPLEMENTATION_PLAN
1. Define ExperimentRecord schema in experiment_registry.py.
2. Implement SQLite store with WAL mode and table experiments.
3. Add auto-capture for git commit SHA and dataset SHA256.
4. Implement query methods: get_best_experiment(metric='val_loss'), list_experiments().
5. Write unit tests testing registration, querying, and manifest generation.

## TEST_PLAN
- `pytest tests/unit/test_experiment_registry.py -v`

## BENCHMARK_PLAN
Register 1,000 experiment records; query top-10 in < 50ms.

## EVIDENCE_REQUIRED
- Code file: src/nexus_scalp/model_lab/experiment_registry.py
- Passing pytest execution output

## ACCEPTANCE_CRITERIA
1. Every experiment produces an immutable JSON manifest and SQLite record.
2. Exact experiment reproduction possible using recorded git SHA, dataset hash, and seed.

## ABORT_CONDITIONS
If git status is dirty and commit SHA cannot be resolved, record 'DIRTY' with working tree diff hash.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `src/nexus_scalp/model_lab/experiment_registry.py`
- `tests/unit/test_experiment_registry.py`

## SHARED_FILE_RISK
Low. AGENT-ML-EXP owns experiment registry.

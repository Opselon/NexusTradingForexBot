# ML-FEAT-002 — Feature Importance, Collinearity Clustering & Redundancy Pruning

STREAM: STREAM B — FEATURE ENGINEERING
PRIORITY: P2
STATUS: DONE
DEPENDENCIES: ML-DATA-001, ML-FEAT-001
AGENT_ROLE: AGENT-FEATURE
OWNERSHIP_SCOPE: src/nexus_scalp/features/importance.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Implement an automated feature evaluation pipeline to compute Permutation Feature Importance (PFI), Mutual Information, and Hierarchical Collinearity Clustering across the 50 base features on out-of-sample data.

## WHY_IT_EXISTS
Many of the 50 features (e.g. multiple EMA distances, lag returns, wick ratios) may exhibit high collinearity (> 0.90) or near-zero predictive alpha. Feeding redundant features increases model parameter count and promotes overfitting.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/features/scalp_features.py` (Lines: `15-70`)
  - **Symbol:** `FEATURE_NAMES`
  - **Behavior:** Defines 50 features including 4 HTF trends, 3 EMA distances, 3 lag returns
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** No documented feature correlation or importance audit
- **Path:** `src/nexus_scalp/features/schema.py` (Lines: `12`)
  - **Symbol:** `ACTIVE_SCHEMA_ID`
  - **Behavior:** Uses all 50 features without pruning
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- 50 features are calculated on every bar.
- No empirical feature importance ranking exists in repository artifacts.

## UNKNOWNs
- Which specific features contribute negatively or neutrally to out-of-sample economic expectancy.

## SCOPE
Develop scripts/analysis/evaluate_feature_importance.py; calculate Spearman correlation matrix; cluster collinear features (threshold > 0.85); compute OOS permutation feature importance drop; publish report.

## NON_GOALS
Do not delete or reorder features in scalp_v1 schema (schema must remain backwards compatible until a new schema version is approved).

## SOURCE_AREAS
- `src/nexus_scalp/features/scalp_features.py`
- `src/nexus_scalp/features/schema.py`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/features/importance.py`
- `scripts/analysis/evaluate_feature_importance.py`
- `docs/research/FEATURE_IMPORTANCE_AUDIT.md`

## INVESTIGATION_PLAN
Inspect whether features exhibit strong non-linear relationships that Pearson correlation misses (use Spearman rank correlation and Mutual Information).

## IMPLEMENTATION_PLAN
1. Implement compute_correlation_matrix() and cluster_collinear_features() in importance.py.
2. Implement compute_permutation_importance() evaluating validation loss drop when shuffling each feature column.
3. Run evaluation script on historical 180-day market dataset.
4. Output docs/research/FEATURE_IMPORTANCE_AUDIT.md detailing top-10 alpha drivers and redundant collinear pairs.
5. Present findings for feature pruning decisions.

## TEST_PLAN
- `pytest tests/unit/test_feature_importance.py -v`

## BENCHMARK_PLAN
Execute PFI on 50,000 validation samples; output ranked importance table and dendrogram cluster map.

## EVIDENCE_REQUIRED
- Report docs/research/FEATURE_IMPORTANCE_AUDIT.md
- Correlation matrix artifact in artifacts/research/

## ACCEPTANCE_CRITERIA
1. Correlation matrix and PFI rankings generated across all 50 features.
2. All collinear pairs with correlation > 0.85 explicitly identified and documented.

## VERIFICATION_EVIDENCE (AGENT-FEATURE, 2026-09-20)
- [x] AC1: `src/nexus_scalp/features/importance.py::compute_correlation_matrix` returns the
  full symmetric 50x50 Spearman matrix; `compute_permutation_importance` returns per-feature
  mean+std ranking. Verified: `tests/unit/test_feature_importance.py::TestCorrelationMatrix`
  (11 tests) + `TestPermutationImportance` (10 tests).
- [x] AC2: `find_collinear_pairs` + `cluster_collinear_features` enumerate every pair above the
  threshold in descending |rho| order and group them into disjoint single-linkage clusters.
  Verified: `TestCollinearity` (12 tests), incl. strict-boundary, chain-merge and
  disjointness assertions.
- [x] `scripts/analysis/evaluate_feature_importance.py` runs the full battery through the REAL
  `ScalpFeatureEngine` + `TripleBarrierLabeler` (never a stub matrix) and emits all three
  artifacts. Verified: `TestRealFeaturePipeline` (5 tests) + `TestCli` (5 tests).
- [x] Determinism: seed-pinned permutations produce byte-identical rankings on re-run
  (`test_deterministic_under_fixed_seed`, `test_evaluation_frame_is_deterministic`).
- [x] Report published: `docs/research/FEATURE_IMPORTANCE_AUDIT.md` (5,945 labelled samples,
  11 collinear pairs, 3 clusters, 8 pruning candidates, top-10 alpha drivers table).
- [x] Dense matrix artifact: `artifacts/research/correlation_matrix.npz` + JSON ranking.
- [x] NON_GOAL honored: zero edits to `scalp_v1` schema, FEATURE_NAMES order or any production
  feature module — `scalp_features.py` and `schema.py` are untouched.

## ABORT_CONDITIONS
If PFI calculation runs out of memory on GPU/CPU, batch the permutation evaluation.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `src/nexus_scalp/features/importance.py`
- `scripts/analysis/evaluate_feature_importance.py`
- `docs/research/FEATURE_IMPORTANCE_AUDIT.md`

## SHARED_FILE_RISK
Low. AGENT-FEATURE owns research feature scripts.

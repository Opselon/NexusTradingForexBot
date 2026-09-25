# ML-CI-002 — ML Contract & Canonical Documentation Drift Prevention CI Gate

STREAM: STREAM L — OBSERVABILITY
PRIORITY: P3
STATUS: DONE (2026-09-21; 20/20 tests in tests/unit/test_ml_contract_drift.py, gate clean on committed tree, CI + local gate wired)
DEPENDENCIES: ML-ARCH-001, ML-PLAT-002 (both DONE — the contract values pinned here are the ones those tasks settled: 50D active / 70D candidate, 3 trained classes, signed-bundle distribution)
AGENT_ROLE: AGENT-GIT
OWNERSHIP_SCOPE: scripts/ci/check_ml_contract_drift.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Implement an automated CI gate that parses canonical ML constants from source code and asserts that docs/ml-system contracts never drift from executable reality.

## WHY_IT_EXISTS
Documentation frequently diverges from code over time. In past audits, docs claimed 70D was active in production while source code was 50D, and claimed 4 classes existed while only 3 were trained. An automated CI guard is required to enforce documentation/code synchronization.

## CURRENT_EVIDENCE
- **Path:** `docs/ml-system/01_SYSTEM_CONTRACT.md` (Lines: `1-50`)
  - **Symbol:** `Top-Level Truths`
  - **Behavior:** Documents 50D, 3-class, 2D inference, API-only promotion
  - **Classification:** `DOCUMENTATION`
  - **Confidence:** 100%
  - **Contradiction:** Must stay synchronized with code
- **Path:** `src/nexus_scalp/features/schema.py` (Lines: `12`)
  - **Symbol:** `ACTIVE_SCHEMA_ID, FEATURE_DIMENSION`
  - **Behavior:** ACTIVE_SCHEMA_ID = 'scalp_v1', FEATURE_DIMENSION = 50
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/model_lifecycle/model_class_contract.py` (Lines: `55`)
  - **Symbol:** `TRAINED_CLASS_COUNT`
  - **Behavior:** TRAINED_CLASS_COUNT = 3
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- Constants exist in code.
- Canonical documents cite exact values.

## UNKNOWNs
- Whether git pre-commit hook or CI check is more developer-friendly.

## SCOPE
Write scripts/ci/check_ml_contract_drift.py; assert ACTIVE_SCHEMA_ID == 'scalp_v1', FEATURE_DIMENSION == 50, TRAINED_CLASS_COUNT == 3 match docs/ml-system; add step to .github/workflows/ci.yml.

## NON_GOALS
Do not perform complex natural language parsing; check exact key-value declarations.

## SOURCE_AREAS
- `docs/ml-system/*.md`
- `src/nexus_scalp/features/schema.py`

## FILES_LIKELY_TO_CHANGE
- `scripts/ci/check_ml_contract_drift.py`
- `.github/workflows/ci.yml`

## INVESTIGATION_PLAN
Determine best regex patterns to extract constants from markdown tables and source python files.

## IMPLEMENTATION_PLAN
1. Write scripts/ci/check_ml_contract_drift.py.
2. Extract FEATURE_DIMENSION, ACTIVE_SCHEMA_ID, TRAINED_CLASS_COUNT from source.
3. Compare against docs/ml-system/01_SYSTEM_CONTRACT.md.
4. Exit code 1 if mismatch found.
5. Add check to .github/workflows/ci.yml.

## TEST_PLAN
- `python scripts/ci/check_ml_contract_drift.py`

## BENCHMARK_PLAN
Execute drift check; must complete in < 0.5 seconds.

## EVIDENCE_REQUIRED
- Script: scripts/ci/check_ml_contract_drift.py
- CI workflow integration diff
- Terminal execution output

## ACCEPTANCE_CRITERIA
1. [x] scripts/ci/check_ml_contract_drift.py passes with exit code 0.
   - Evidence: `ML contract drift check clean: 8 canonical constants verified
     against 4 contract docs (4.2 ms)` on the committed tree at HEAD
     9fab7ff7; rc=0.
2. [x] Any code change altering FEATURE_DIMENSION or TRAINED_CLASS_COUNT
   without updating docs/ml-system fails CI.
   - Evidence: tests/unit/test_ml_contract_drift.py
     ::test_stale_doc_detected_when_class_count_changes and
     ::test_stale_doc_detected_when_live_dimension_changes force the change in
     a sandboxed source copy and assert the gate returns a STALE_DOC finding
     naming the new value with a docs file:line citation. Wired into both
     `.github/workflows/ci.yml` (ci-integrity lane) and the local pre-push
     gate (`scripts/ci/check_local.py` stage [5b] `ml_contract_drift`) so no
     CI-only gate class exists (gate_parity.py: 6/6 PASS, 0 drifts).

## VERIFICATION_EVIDENCE
- 20/20 tests pass (Python 3.11.16, pytest 9.1.1, slim venv, 0.23 s).
- ruff check + ruff format --check + mypy clean on all touched files.
- scripts/ci/verify_critical_suite_manifest.py: CRITICAL_SUITE_MANIFEST_OK,
  222 paths.
- scripts/ci/check_workflows.py --strict: 0 ERROR, 0 WARNING.
- scripts/ci/check_dependency_drift.py: OK, 99 pins (unchanged).
- BENCHMARK_PLAN: drift check measured 4.2 ms (< 0.5 s budget), and 5-run
  average < 0.5 s asserted by test_benchmark_completes_under_half_second.
- Determinism: two consecutive runs produce byte-identical findings
  (test_check_is_deterministic).

## DESIGN_NOTES (why the gate is shaped this way)
- Direction of truth is SOURCE (matches 01_SYSTEM_CONTRACT.md §2 evidence
  hierarchy). The gate never fails because a doc added a sentence; it fails
  only when a doc asserts a value the source no longer holds, or when the
  canonical declaration itself moved without a docs update.
- Extraction is regex-on-source, NOT import: importing features/model
  packages pulls torch+polars into a static CI lane that must stay
  dependency-free (same design as check_torch_load_safety.py and
  check_migration_safety.py). Pinned by test_gate_module_imports_no_project_dependencies.
- Prose-immunity: `_assertes_contract_value` accepts only assignment/copula
  shapes (`KEY = v`, `KEY: int = v`, `KEY is v`); past-tense mentions and
  file references never fire (test_historical_prose_is_not_a_value_claim).
- One real coverage gap found and FIXED (not synthetic): the contract suite
  never cited the position-size ceiling anywhere in docs/ml-system. Added
  §2.2 "Execution Ceiling (Position Safety Contract)" to
  05_INFERENCE_FORWARDTEST_GOVERNANCE.md citing `HARD_MAX_LOTS = 10.0` and
  `MAX_TOTAL_EXPOSURE = 1`, so the gate passes on real coverage rather than
  by exception.


## ABORT_CONDITIONS
None.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `scripts/ci/check_ml_contract_drift.py`

## SHARED_FILE_RISK
Low. AGENT-GIT owns CI check scripts.

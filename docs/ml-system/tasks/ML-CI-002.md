# ML-CI-002 — ML Contract & Canonical Documentation Drift Prevention CI Gate

STREAM: STREAM L — OBSERVABILITY
PRIORITY: P3
STATUS: BLOCKED
DEPENDENCIES: ML-ARCH-001, ML-PLAT-002
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
1. scripts/ci/check_ml_contract_drift.py passes with exit code 0.
2. Any code change altering FEATURE_DIMENSION or TRAINED_CLASS_COUNT without updating docs/ml-system fails CI.

## ABORT_CONDITIONS
None.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `scripts/ci/check_ml_contract_drift.py`

## SHARED_FILE_RISK
Low. AGENT-GIT owns CI check scripts.

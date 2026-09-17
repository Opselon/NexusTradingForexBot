# TASK-015 — ML Contract & Canonical Documentation Drift Prevention CI Gate

Priority: P3
Status: EXECUTABLE AFTER TASK-003, TASK-007
Type: Governance & CI/CD
Dependencies: TASK-003, TASK-007
Blocks: None
Human Decision Required: NO
Risk: Low (Automated CI validation script; zero production runtime impact)
Estimated Scope: 1 CI verification script (scripts/ci/check_ml_contract_drift.py) + workflow hook (~120 LOC)

## Objective
Implement an automated CI gate that parses canonical ML constants from source code and asserts that docs/ml-system contracts never drift from executable reality.

## Problem / Why
Documentation frequently diverges from code over time. In past audits, docs claimed 70D was active in production while source code was 50D, and claimed 4 classes existed while only 3 were trained. An automated CI guard is required to enforce documentation/code synchronization.

## Current Evidence
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

## Scope
1. Write scripts/ci/check_ml_contract_drift.py.
2. Assert that ACTIVE_SCHEMA_ID in schema.py matches the schema cited in 01_SYSTEM_CONTRACT.md.
3. Assert that FEATURE_DIMENSION == 50 and TRAINED_CLASS_COUNT == 3.
4. Hook script into .github/workflows/ci.yml as a required check.

## Non-Goals
Do not perform complex natural language parsing; check exact key-value declarations.

## Preconditions
TASK-003 and TASK-007 complete.

## Dependencies
TASK-003, TASK-007

## Blocks
None

## Source Areas
- `docs/ml-system/*.md`
- `scripts/ci/check_ml_contract_drift.py`
- `.github/workflows/ci.yml`

## Investigation
Determine best regex patterns to extract constants from markdown tables and source python files.

## Implementation Plan
1. Write scripts/ci/check_ml_contract_drift.py checking:
   - FEATURE_DIMENSION == 50
   - ACTIVE_SCHEMA_ID == 'scalp_v1'
   - TRAINED_CLASS_COUNT == 3
   - MIN_ECONOMIC_OOS_EXPECTANCY_R == 0.02
2. Compare extracted values against docs/ml-system/01_SYSTEM_CONTRACT.md and 02_DATA_CONTRACT.md.
3. If mismatch found, exit with status 1 and report exact discrepancy.
4. Add step to .github/workflows/ci.yml.
5. Run script locally and verify status 0.

## Tests
- `python scripts/ci/check_ml_contract_drift.py`

## Validation / Benchmark
Script returns exit code 0 when contracts match; returns exit code 1 with descriptive diff when values are intentionally modified.

## Evidence Required
- Script: scripts/ci/check_ml_contract_drift.py
- Terminal output showing clean execution and exit 0
- CI workflow integration diff

## Acceptance Criteria
1. scripts/ci/check_ml_contract_drift.py passes with exit code 0.
2. Any code change altering FEATURE_DIMENSION or TRAINED_CLASS_COUNT without updating docs/ml-system fails the CI check.

## Failure / Abort Conditions
None.

## Human Stop Conditions
None.

## Expected Output
Automated CI gate permanently preventing documentation drift from code reality.

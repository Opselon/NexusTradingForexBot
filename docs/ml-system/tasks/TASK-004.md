# TASK-004 — Model Promotion Pipeline Pre-Flight & OOS Economic Gate Verification

Priority: P0
Status: EXECUTABLE AFTER TASK-001
Type: Governance & Security
Dependencies: TASK-001
Blocks: TASK-007
Human Decision Required: NO
Risk: High (Governs real-money Champion model deployment)
Estimated Scope: Integration test suite, pre-flight route verification (~200 LOC)

## Objective
Harden and verify the POST /api/models/promotion/execute transaction pipeline, ensuring no candidate can become Champion without passing the OOS economic expectancy floor (>= 0.02R) and acquiring the exclusive PromotionLock.

## Problem / Why
Model promotion replaces the active neural network driving live MT5 orders. Any flaw in pre-flight checks, concurrent execution, or rollback journaling could corrupt the active Champion model bundle or promote an unvalidated model.

## Current Evidence
- **Path:** `src/nexus_scalp/web/model_governance_routes.py` (Lines: `1270-1410`)
  - **Symbol:** `execute_promotion`
  - **Behavior:** Single entry point for model promotion; requires actor, token, candidate_id; calls ModelGovernanceEngine.execute_promotion
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** API-only; no CLI or UI execute button
- **Path:** `src/nexus_scalp/governance/transaction.py` (Lines: `45-210`)
  - **Symbol:** `PromotionTransaction`
  - **Behavior:** Acquires PromotionLock; verifies old champion hash; creates rollback journal; swaps files; commits DB
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/research/oos.py` (Lines: `35-90`)
  - **Symbol:** `OOSGate, MIN_ECONOMIC_OOS_EXPECTANCY_R`
  - **Behavior:** MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02; rejects candidates with expectancy < 0.02R
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None

## Scope
1. Write tests/integration/test_model_promotion_pipeline_e2e.py testing the full execution of POST /api/models/promotion/execute.
2. Verify that candidates with OOS expectancy < 0.02R are rejected with HTTP 422.
3. Verify that invalid operator tokens are rejected with HTTP 401/403.
4. Verify that concurrent promotion requests are serialized or rejected via PromotionLock.
5. Test simulated filesystem failure during file swap to confirm rollback journal restores the original Champion without corruption.

## Non-Goals
Do not build a UI promotion button (promotion must remain strictly API-driven with signed operator authorization).

## Preconditions
TASK-001 complete; FastAPI test client configured.

## Dependencies
TASK-001

## Blocks
TASK-007 (Signed Official Bundle Distribution)

## Source Areas
- `src/nexus_scalp/web/model_governance_routes.py:1250-1420`
- `src/nexus_scalp/governance/transaction.py:1-250`
- `src/nexus_scalp/governance/engine.py:750-860`
- `src/nexus_scalp/research/oos.py:1-120`

## Investigation
Inspect ModelGovernanceEngine.execute_promotion to verify whether gate_oos is checked synchronously during the promotion transaction or assumed to have been verified in a previous lifecycle stage.

## Implementation Plan
1. Create tests/integration/test_model_promotion_pipeline_e2e.py.
2. Mock a valid candidate bundle (model.pt, model.scaler.npz, model.meta.json) and an active champion bundle.
3. Test successful promotion: candidate files atomically copied to champion slot, manifest updated, audit event logged.
4. Test OOS rejection: candidate with oos_expectancy = 0.01R must fail gate check.
5. Test lock contention: second concurrent promotion request raises PromotionLockError.
6. Test journal rollback: inject IOError during model.pt copy and assert original champion files remain intact.

## Tests
- `pytest tests/integration/test_model_promotion_pipeline_e2e.py -v`
- `pytest tests/unit/test_governance_transaction.py -v`

## Validation / Benchmark
Verify fail-closed behavior: 100% of invalid promotion attempts leave active Champion hash unchanged in governance.db.

## Evidence Required
- Test file: tests/integration/test_model_promotion_pipeline_e2e.py
- Passing pytest execution log covering success, lock contention, OOS failure, and rollback journal restoration

## Acceptance Criteria
1. POST /api/models/promotion/execute requires valid operator token and un-frozen governance.
2. Candidates failing OOS economic expectancy (< 0.02R) cannot be promoted.
3. Failed promotions trigger automatic atomic rollback with zero file corruption.

## Failure / Abort Conditions
If promotion transaction does not implement atomic rollback or leaves orphaned temporary files, STOP and fix transaction safety.

## Human Stop Conditions
None.

## Expected Output
Thoroughly tested and hardened model promotion route with automated rollback verification.

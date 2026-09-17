# ML-GOV-001 — Model Promotion Pipeline Pre-Flight & OOS Economic Gate Verification

STREAM: STREAM J — GOVERNANCE
PRIORITY: P0
STATUS: BLOCKED
DEPENDENCIES: ML-FEAT-001
AGENT_ROLE: AGENT-GOVERNANCE
OWNERSHIP_SCOPE: src/nexus_scalp/web/model_governance_routes.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Verify and harden the POST /api/models/promotion/execute transaction pipeline, ensuring no candidate can become Champion without passing the OOS economic expectancy floor (>= 0.02R) and acquiring the exclusive PromotionLock.

## WHY_IT_EXISTS
Model promotion replaces the active neural network driving live MT5 orders. Any flaw in pre-flight checks, concurrent execution, or rollback journaling could corrupt the active Champion model bundle or promote an unvalidated model.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/web/model_governance_routes.py` (Lines: `1270-1410`)
  - **Symbol:** `execute_promotion`
  - **Behavior:** Single promotion entry point; requires actor, token, candidate_id; calls ModelGovernanceEngine.execute_promotion
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

## FACTS
- Promotion is API-only.
- Requires PromotionLock.
- Requires OOS expectancy >= 0.02R.

## UNKNOWNs
- Recovery behavior if server experiences power loss mid-filesystem copy.

## SCOPE
Write tests/integration/test_model_promotion_pipeline_e2e.py testing successful promotion, token rejection, lock contention, OOS failure, and simulated rollback journal recovery.

## NON_GOALS
Do not build a web UI promotion button (must remain API-driven with operator auth tokens).

## SOURCE_AREAS
- `src/nexus_scalp/web/model_governance_routes.py`
- `src/nexus_scalp/governance/transaction.py`
- `src/nexus_scalp/research/oos.py`

## FILES_LIKELY_TO_CHANGE
- `tests/integration/test_model_promotion_pipeline_e2e.py`

## INVESTIGATION_PLAN
Verify whether gate_oos is checked synchronously during the promotion transaction or assumed to have passed previously.

## IMPLEMENTATION_PLAN
1. Create tests/integration/test_model_promotion_pipeline_e2e.py.
2. Mock valid candidate bundle and active champion bundle.
3. Test valid promotion: files copied, manifest committed, audit event logged.
4. Test OOS rejection: candidate with oos_expectancy = 0.01R rejected with HTTP 422.
5. Test lock contention: concurrent request raises PromotionLockError.
6. Test journal rollback: inject IOError during file swap; assert original champion untouched.

## TEST_PLAN
- `pytest tests/integration/test_model_promotion_pipeline_e2e.py -v`

## BENCHMARK_PLAN
Measure promotion transaction execution time (< 500ms).

## EVIDENCE_REQUIRED
- Test file: tests/integration/test_model_promotion_pipeline_e2e.py
- Passing pytest execution log

## ACCEPTANCE_CRITERIA
1. POST /api/models/promotion/execute requires valid operator token.
2. Candidates failing OOS economic expectancy (< 0.02R) cannot be promoted.
3. Failed promotions trigger automatic atomic rollback.

## ABORT_CONDITIONS
If promotion transaction leaves orphaned lock files or corrupts champion slot, abort.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `tests/integration/test_model_promotion_pipeline_e2e.py`

## SHARED_FILE_RISK
Low. AGENT-GOVERNANCE owns governance test.

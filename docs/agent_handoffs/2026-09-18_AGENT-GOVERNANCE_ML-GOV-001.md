# ML-GOV-001 Handoff — Model Promotion Pipeline Pre-Flight & OOS Economic Gate Verification

## Task Summary
- **Task ID**: `ML-GOV-001`
- **Agent**: `AGENT-GOVERNANCE`
- **Date**: `2026-09-18`
- **Branch**: `agent/feature/ML-GOV-001`
- **Status**: `IMPLEMENTED` (Ready for PR & Merge)
- **Goal**: Verify and harden the `POST /api/models/promotion/execute` transaction pipeline, ensuring no candidate can become Champion without passing the OOS economic expectancy floor (>= 0.02R) and acquiring the exclusive PromotionLock.

## Key Changes
1. `src/nexus_scalp/web/model_governance_routes.py`:
   - Enforced OOS economic expectancy floor (`MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02R`) on `POST /api/models/promotion/execute`.
   - Rejects candidates below 0.02R or with negative expectancy with `PROMOTION_BLOCKED` and `gate: oos_economic_floor`.
   - Forwarded `oos_artifact` and `manifest` into `execute_promotion_transaction`.
   - Dynamically bound `_get_active_app` to current FastAPI instance state.
2. `tests/integration/test_model_promotion_pipeline_e2e.py`:
   - 8 comprehensive end-to-end integration tests:
     - Missing actor/token/model_id validation.
     - OOS economic expectancy floor rejection (< 0.02R and negative).
     - Emergency promotion freeze enforcement.
     - Exclusive PromotionLock contention and collision guard (`PROMOTION_CONFLICT`).
     - Automatic atomic rollback on model activation error.
     - Automatic rollback on post-activation verification failure (`rollback_activate` restoring previous Champion).
     - Happy-path promotion transaction and benchmark execution (< 500ms SLA).
3. `tests/critical_suite.txt`:
   - Registered `tests/integration/test_model_promotion_pipeline_e2e.py` (202 paths).

## Verification Evidence
- `pytest tests/integration/test_model_promotion_pipeline_e2e.py -v`: 8 passed in 11.49s.
- `ruff check`: All checks passed.
- `ruff format`: Formatted cleanly.
- `mypy src`: Success: no issues found in 612 source files.
- `mypy tests/integration/test_model_promotion_pipeline_e2e.py`: Success: no issues found.
- `verify_critical_suite_manifest.py`: CRITICAL_SUITE_MANIFEST_OK: 202 paths all exist.
- `scripts/docs/build_site.py && scripts/docs/check_docs.py`: DOCS_HEALTH = PASS.

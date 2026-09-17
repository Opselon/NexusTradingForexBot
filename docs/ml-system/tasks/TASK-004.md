# TASK-004 — Model Promotion & OOS Economic Expectancy Gate Audit

## Priority
P0

## Status
PENDING

## Objective
Verify and harden the POST /api/models/promotion/execute pipeline, ensuring candidates must pass OOS economic expectancy before promotion.

## Why This Task Exists
Promotion is the single transition point from research candidate to real-money Champion; gates must be impenetrable.

## Current Evidence
src/nexus_scalp/web/model_governance_routes.py:1270; src/nexus_scalp/governance/transaction.py:1-450.

## Scope
Harden promotion transaction pre-flight checks: assert OOSGate expectancy > 0.02R, verification of old champion SHA256, and atomic rollback journal.

## Explicit Non-Goals
Do not build a UI promotion button (promotion remains strictly API-driven with operator tokens).

## Dependencies
TASK-001

## Source Areas
src/nexus_scalp/web/model_governance_routes.py, src/nexus_scalp/governance/transaction.py, src/nexus_scalp/research/oos.py

## Files Likely Involved
src/nexus_scalp/web/model_governance_routes.py, src/nexus_scalp/governance/transaction.py, src/nexus_scalp/research/oos.py

## Investigation Required
Inspect rollback journal recovery in the event of power/process crash mid-copy.

## Implementation Outline
Add automated integration tests exercising promotion transaction with valid and invalid candidates.

## Tests Required
tests/integration/test_promotion_governance_e2e.py

## Benchmark Requirements
Run critical suite to confirm zero regressions.

## Evidence Required
Exact execution trace and test output proving acceptance criteria are met.

## Acceptance Criteria
Invalid candidate is rejected fail-closed; valid candidate cleanly promoted with journal cleanup.

## Human Stop Conditions
None required.

## Expected Output
Tested, verified PR with passing tests and updated task status.

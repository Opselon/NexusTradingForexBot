# TASK-010 — 50D vs 70D Architectural Reconciliation & Migration Roadmap

## Priority
P2

## Status
PENDING

## Objective
Formalize the decision framework and migration path for upgrading live trading from 50D (scalp_v1) to 70D (scalp_v3).

## Why This Task Exists
70D incorporates News and Liquidity intelligence but is blocked from live execution due to schema contracts and missing champion bundles.

## Current Evidence
src/nexus_scalp/features/schema.py:12; src/nexus_scalp/features/schema_contract.py:63; src/nexus_scalp/features/runtime70.py.

## Scope
Define formal migration gates: 1. Live liquidity governor certification, 2. News feature stability, 3. 70D Champion training and OOS validation.

## Explicit Non-Goals
Do not prematurely flip ACTIVE_SCHEMA_ID to scalp_v3 before 70D Champion is trained and verified.

## Dependencies
TASK-001, TASK-002

## Source Areas
src/nexus_scalp/features/schema.py, src/nexus_scalp/features/features70.py, src/nexus_scalp/application/live/inference.py

## Files Likely Involved
src/nexus_scalp/features/schema.py, src/nexus_scalp/features/features70.py, src/nexus_scalp/application/live/inference.py

## Investigation Required
Assess real-time feed reliability for News and Liquidity features.

## Implementation Outline
Write formal migration test suite validating 70D vector assembly under live tick simulator.

## Tests Required
tests/unit/test_70d_live_migration_gate.py

## Benchmark Requirements
Run critical suite to confirm zero regressions.

## Evidence Required
Exact execution trace and test output proving acceptance criteria are met.

## Acceptance Criteria
Comprehensive report and migration checklist completed with zero breaking changes to 50D path.

## Human Stop Conditions
HUMAN DECISION REQUIRED: Approve migration criteria for adopting 70D as live Champion schema.

## Expected Output
Tested, verified PR with passing tests and updated task status.

# TASK-006 — Unified Model State Machine Root

## Priority
P1

## Status
PENDING

## Objective
Reconcile ModelStatus (lifecycle) and PromotionState (governance) into a single authoritative state root.

## Why This Task Exists
Parallel state machines with differing vocabularies store duplicate model status across SQLite tables, risking divergent states.

## Current Evidence
model_lifecycle/models.py:32 (ModelStatus); governance/models.py:170 (PromotionState); model_lab/registry.py:31 (LabStatus).

## Scope
Create a canonical state adapter mapping PromotionState <-> ModelStatus with strict invariants.

## Explicit Non-Goals
Do not alter lab-only internal states.

## Dependencies
None

## Source Areas
src/nexus_scalp/governance/models.py, src/nexus_scalp/model_lifecycle/models.py, src/nexus_scalp/governance/alignment.py

## Files Likely Involved
src/nexus_scalp/governance/models.py, src/nexus_scalp/model_lifecycle/models.py, src/nexus_scalp/governance/alignment.py

## Investigation Required
Map all state transitions between CANDIDATE, CHALLENGER, VALIDATED, SHADOW, and CHAMPION.

## Implementation Outline
Implement synchronization hook in ModelGovernanceEngine to update ModelLifecycleStore state atomically.

## Tests Required
tests/unit/test_state_machine_reconciliation.py

## Benchmark Requirements
Run critical suite to confirm zero regressions.

## Evidence Required
Exact execution trace and test output proving acceptance criteria are met.

## Acceptance Criteria
Querying model state through either lifecycle or governance API returns synchronized, consistent status.

## Human Stop Conditions
HUMAN DECISION REQUIRED: Approve mapping table between ModelStatus and PromotionState.

## Expected Output
Tested, verified PR with passing tests and updated task status.

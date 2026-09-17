# TASK-006 — Single Source of Truth Model State Machine Unification

Priority: P1
Status: HUMAN DECISION REQUIRED
Type: Architecture & Data Governance
Dependencies: None
Blocks: TASK-008
Human Decision Required: YES (Designate whether ModelStatus or PromotionState is the single root authority)
Risk: High (Database schema and state transition refactoring across lifecycle and governance stores)
Estimated Scope: State mapping adapter, schema migration, synchronization hooks (~250 LOC)

## Objective
Reconcile the three competing state machines (ModelStatus in lifecycle.db, PromotionState in governance.db, LabStatus in lab_experiments.db) into a single authoritative state hierarchy.

## Problem / Why
Multiple state machines with differing vocabularies store duplicate model status across separate SQLite databases. A model can be registered as CHALLENGER in model_lifecycle while remaining RESEARCH in governance, risking split-brain state decisions during deployment.

## Current Evidence
- **Path:** `src/nexus_scalp/model_lifecycle/models.py` (Lines: `32-42`)
  - **Symbol:** `ModelStatus`
  - **Behavior:** Enum: CANDIDATE, CHALLENGER, CHAMPION, REJECTED, ARCHIVED, INVALID
  - **Classification:** `LIFECYCLE`
  - **Confidence:** 100%
  - **Contradiction:** Direct CANDIDATE -> CHAMPION transition
- **Path:** `src/nexus_scalp/governance/models.py` (Lines: `170-195`)
  - **Symbol:** `PromotionState`
  - **Behavior:** Enum: RESEARCH, VALIDATED, CHALLENGER, SHADOW, READY_FOR_REVIEW, APPROVED, CHAMPION, REJECTED, RETIRED
  - **Classification:** `GOVERNANCE`
  - **Confidence:** 100%
  - **Contradiction:** More granular; used for production promotion
- **Path:** `src/nexus_scalp/model_lab/registry.py` (Lines: `31-45`)
  - **Symbol:** `LabStatus`
  - **Behavior:** Enum: CREATED, TRAINING, COMPLETED, FAILED, REJECTED, VALIDATED, PROMOTION_CANDIDATE
  - **Classification:** `LAB-ONLY`
  - **Confidence:** 100%
  - **Contradiction:** Sandboxed vocabulary

## Scope
1. Formulate formal state alignment proposal: Declare PromotionState (governance.db) as the sole Authoritative State of Record for production models.
2. Implement a bidirectional synchronization adapter mapping PromotionState <-> ModelStatus.
3. Require operator decision to approve mapping rules before applying DB changes.

## Non-Goals
Do not modify the internal sandbox states of Model Lab (LabStatus remains isolated).

## Preconditions
Human operator approval of canonical state mapping.

## Dependencies
None

## Blocks
TASK-008 (Governance Freeze Persistence)

## Source Areas
- `src/nexus_scalp/governance/models.py:165-210`
- `src/nexus_scalp/model_lifecycle/models.py:25-60`
- `src/nexus_scalp/governance/engine.py:200-350`

## Investigation
Trace all queries in LiveEngine and InferenceService to verify whether they read model state from lifecycle.db or governance.db.

## Implementation Plan
1. STOP for Human Decision: Operator reviews proposed state mapping table:
   - PromotionState.RESEARCH <-> ModelStatus.CANDIDATE
   - PromotionState.VALIDATED <-> ModelStatus.CANDIDATE
   - PromotionState.CHALLENGER <-> ModelStatus.CHALLENGER
   - PromotionState.SHADOW <-> ModelStatus.CHALLENGER
   - PromotionState.CHAMPION <-> ModelStatus.CHAMPION
   - PromotionState.REJECTED <-> ModelStatus.REJECTED
   - PromotionState.RETIRED <-> ModelStatus.ARCHIVED
2. Once approved, create src/nexus_scalp/governance/state_adapter.py.
3. Hook ModelGovernanceEngine updates to synchronize ModelLifecycleStore atomically.
4. Add unit test verifying that updating state in governance reflects identically when queried via lifecycle.

## Tests
- `pytest tests/unit/test_state_machine_reconciliation.py -v`

## Validation / Benchmark
Zero discrepancies between governance_models table and model_registry table for all active models.

## Evidence Required
- Operator signed approval of state mapping
- Code diff: state adapter implementation
- Unit tests demonstrating synchronized state updates across both stores

## Acceptance Criteria
1. Single authoritative state root established.
2. Querying model status via governance or lifecycle API returns consistent, non-contradictory status values.

## Failure / Abort Conditions
If operator rejects the proposed mapping table, STOP and revise mapping options.

## Human Stop Conditions
HUMAN STOP CONDITION: Operator must approve which state machine is authoritative.

## Expected Output
Unified state adapter and synchronization mechanism preventing model split-brain states.

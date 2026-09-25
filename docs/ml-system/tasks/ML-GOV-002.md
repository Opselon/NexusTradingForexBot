# ML-GOV-002 — Single Source of Truth Model State Machine Unification

STREAM: STREAM J — GOVERNANCE
PRIORITY: P1
STATUS: HUMAN DECISION REQUIRED
DEPENDENCIES: None
AGENT_ROLE: AGENT-GOVERNANCE
OWNERSHIP_SCOPE: src/nexus_scalp/governance/state_adapter.py
HUMAN_DECISION_REQUIRED: YES (Operator must designate authoritative state machine root)
PARALLELIZATION_CLASS: REQUIRES_DECISION

## OBJECTIVE
Reconcile the three competing state machines (ModelStatus in lifecycle.db, PromotionState in governance.db, LabStatus in lab_experiments.db) into a single authoritative state root.

## WHY_IT_EXISTS
Multiple state machines with differing vocabularies store duplicate model status across separate SQLite databases. A model can be registered as CHALLENGER in model_lifecycle while remaining RESEARCH in governance, risking split-brain state decisions during deployment.

## CURRENT_EVIDENCE
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

## FACTS
- 3 distinct state enums exist.
- Lifecycle and governance databases maintain independent model records.

## UNKNOWNs
- Operator preference for authoritative status vocabulary.

## SCOPE
Formulate state alignment proposal: Declare PromotionState as authoritative root; implement bidirectional adapter; STOP for operator decision; upon approval, apply synchronization hooks.

## NON_GOALS
Do not modify internal sandbox states of Model Lab.

## SOURCE_AREAS
- `src/nexus_scalp/governance/models.py`
- `src/nexus_scalp/model_lifecycle/models.py`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/governance/state_adapter.py`
- `tests/unit/test_state_machine_reconciliation.py`

## INVESTIGATION_PLAN
Audit all LiveEngine and InferenceService queries to confirm which DB is queried for active model state.

## IMPLEMENTATION_PLAN
1. STOP for Human Decision: Operator reviews proposed state mapping table.
2. Once approved, implement src/nexus_scalp/governance/state_adapter.py.
3. Hook ModelGovernanceEngine updates to synchronize ModelLifecycleStore atomically.
4. Add unit test asserting synchronized state updates across both stores.

## TEST_PLAN
- `pytest tests/unit/test_state_machine_reconciliation.py -v`

## BENCHMARK_PLAN
Measure state transition query latency across SQLite stores (< 5ms).

## EVIDENCE_REQUIRED
- Recorded operator decision
- Code file: src/nexus_scalp/governance/state_adapter.py
- Passing pytest execution output

## ACCEPTANCE_CRITERIA
1. Single authoritative state root established.
2. Querying model status via governance or lifecycle API returns consistent, non-contradictory status values.

## ABORT_CONDITIONS
If operator rejects mapping proposal, revise mapping options.

## HUMAN_DECISION_REQUIRED
YES (Operator must designate authoritative state machine root)

## EXPECTED_ARTIFACTS
- `src/nexus_scalp/governance/state_adapter.py`
- `docs/decisions/DEC-006_STATE_MACHINE.md`

## SHARED_FILE_RISK
Medium. AGENT-GOVERNANCE coordinates with AGENT-ML-EXP.

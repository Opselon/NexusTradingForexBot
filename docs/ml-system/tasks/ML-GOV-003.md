# ML-GOV-003 — Persistent Governance Emergency Freeze Across Process Restarts

STREAM: STREAM J — GOVERNANCE
PRIORITY: P1
STATUS: BLOCKED
DEPENDENCIES: ML-GOV-002
AGENT_ROLE: AGENT-GOVERNANCE
OWNERSHIP_SCOPE: src/nexus_scalp/governance/engine.py, store.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Persist the emergency promotion freeze state (promotion_frozen, disabled_candidates) in SQLite so that process crashes or daemon restarts cannot accidentally clear an active emergency stop.

## WHY_IT_EXISTS
Currently, ModelGovernanceEngine.promotion_frozen is stored only in process memory (self.promotion_frozen = False in __init__). While freeze actions are logged to SQLite as audit events, the boolean flag itself is not reloaded on startup. A daemon restart automatically unfreezes promotions, violating operational safety.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/governance/engine.py` (Lines: `107-112`)
  - **Symbol:** `ModelGovernanceEngine.__init__`
  - **Behavior:** self.promotion_frozen: bool = False; self.disabled_candidates: set[str] = set() (In-memory by design comment)
  - **Classification:** `GOVERNANCE`
  - **Confidence:** 100%
  - **Contradiction:** Freeze resets to False on restart
- **Path:** `src/nexus_scalp/governance/engine.py` (Lines: `848-865`)
  - **Symbol:** `ModelGovernanceEngine.freeze_promotions`
  - **Behavior:** Sets self.promotion_frozen = True; records event to governance_events table
  - **Classification:** `GOVERNANCE`
  - **Confidence:** 100%
  - **Contradiction:** Event is logged but state is not restored

## FACTS
- promotion_frozen is an in-memory boolean.
- Process reboot clears freeze back to False.

## UNKNOWNs
- SQLite write lock performance under rapid freeze/unfreeze cycles.

## SCOPE
Create governance_state table in governance.db; persist promotion_frozen = '1' on freeze; restore on boot in __init__; add unit test asserting persistence across instance re-instantiation.

## NON_GOALS
Do not change the HTTP API signatures for /freeze or /unfreeze.

## SOURCE_AREAS
- `src/nexus_scalp/governance/engine.py`
- `src/nexus_scalp/governance/store.py`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/governance/engine.py`
- `src/nexus_scalp/governance/store.py`
- `tests/unit/test_governance_freeze_persistence.py`

## INVESTIGATION_PLAN
Verify whether GovernanceStore schema migrations support adding new tables without data loss.

## IMPLEMENTATION_PLAN
1. Add governance_state table to GovernanceStore schema.
2. Add get_state() and set_state() methods to GovernanceStore.
3. Update ModelGovernanceEngine.__init__ to load promotion_frozen on boot.
4. Update freeze_promotions and unfreeze_promotions to persist state atomically.
5. Write tests/unit/test_governance_freeze_persistence.py.

## TEST_PLAN
- `pytest tests/unit/test_governance_freeze_persistence.py -v`

## BENCHMARK_PLAN
Re-instantiate engine 100 times; assert state remains frozen in 100% of instantiations.

## EVIDENCE_REQUIRED
- Code diff in engine.py and store.py
- Pytest output verifying persistent state recovery

## ACCEPTANCE_CRITERIA
1. Calling freeze_promotions() persists state in SQLite.
2. A newly created ModelGovernanceEngine instance starts in FROZEN state if previously frozen.
3. unfreeze_promotions() correctly clears persistent state.

## ABORT_CONDITIONS
If SQLite table migration fails or corrupts existing governance.db data, rollback schema.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `tests/unit/test_governance_freeze_persistence.py`

## SHARED_FILE_RISK
Medium. AGENT-GOVERNANCE owns engine.py and store.py.

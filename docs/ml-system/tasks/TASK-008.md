# TASK-008 — Persistent Governance Emergency Freeze Across Process Restarts

Priority: P1
Status: EXECUTABLE AFTER TASK-006
Type: Operational Safety
Dependencies: TASK-006
Blocks: TASK-012
Human Decision Required: NO
Risk: Medium (Database schema update in governance.db)
Estimated Scope: SQLite persistence for emergency freeze, initialization hook (~80 LOC)

## Objective
Persist the emergency promotion freeze state (self.promotion_frozen, disabled_candidates) in SQLite so that process crashes or daemon restarts cannot accidentally clear an active emergency stop.

## Problem / Why
Currently, ModelGovernanceEngine.promotion_frozen is stored only in process memory (self.promotion_frozen = False in __init__). While freeze actions are logged to SQLite as audit events, the boolean flag itself is not reloaded on startup. A daemon restart automatically unfreezes promotions, violating operational safety.

## Current Evidence
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

## Scope
1. Create governance_state table in governance.db storing key-value pairs (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT).
2. Update ModelGovernanceEngine.freeze_promotions to persist promotion_frozen = '1'.
3. Update ModelGovernanceEngine.unfreeze_promotions to persist promotion_frozen = '0'.
4. Update ModelGovernanceEngine.__init__ to query governance_state and restore promotion_frozen on startup.
5. Write unit test verifying freeze persistence across instance re-instantiation.

## Non-Goals
Do not change the HTTP API signatures for /freeze or /unfreeze.

## Preconditions
TASK-006 complete; GovernanceStore available.

## Dependencies
TASK-006

## Blocks
TASK-012 (Online Fine-Tuning Sandbox)

## Source Areas
- `src/nexus_scalp/governance/engine.py:100-130, 840-880`
- `src/nexus_scalp/governance/store.py:1-250`

## Investigation
Check whether GovernanceStore already has generic key-value metadata tables or if a dedicated table is cleaner.

## Implementation Plan
1. Add table governance_state to GovernanceStore migration script.
2. Add methods get_state(key), set_state(key, value) to GovernanceStore.
3. Update ModelGovernanceEngine.__init__ to load promotion_frozen and disabled_candidates from store.
4. Update freeze_promotions and unfreeze_promotions to persist state atomically with audit event.
5. Create tests/unit/test_governance_freeze_persistence.py.

## Tests
- `pytest tests/unit/test_governance_freeze_persistence.py -v`
- `pytest tests/unit/test_governance_engine.py -v`

## Validation / Benchmark
Instantiate engine -> freeze_promotions() -> delete engine instance -> instantiate new engine instance -> assert promotion_frozen == True.

## Evidence Required
- Code diff in engine.py and store.py
- Pytest output verifying persistent state recovery across multiple process instances

## Acceptance Criteria
1. Calling freeze_promotions() persists state in SQLite.
2. A newly created ModelGovernanceEngine instance starts in FROZEN state if previously frozen.
3. unfreeze_promotions() correctly clears persistent state.

## Failure / Abort Conditions
If SQLite table migration fails or corrupts existing governance.db data, STOP and rollback schema.

## Human Stop Conditions
None.

## Expected Output
Crash-resilient emergency freeze mechanism that guarantees promotion safety across system reboots.

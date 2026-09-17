# TASK-008 — Governance Freeze Persistence Across Process Restarts

## Priority
P1

## Status
PENDING

## Objective
Persist emergency freeze state (promotion_frozen, disabled_candidates) to SQLite so that process restarts do not accidentally clear it.

## Why This Task Exists
Currently, self.promotion_frozen is in-memory only. A crash or reboot clears the emergency stop, creating operational risk.

## Current Evidence
src/nexus_scalp/governance/engine.py:108-110, 848-860.

## Scope
Persist freeze state in governance_models metadata or dedicated governance_state table; restore on boot.

## Explicit Non-Goals
Do not alter the freeze API signatures.

## Dependencies
TASK-006

## Source Areas
src/nexus_scalp/governance/engine.py, src/nexus_scalp/governance/store.py

## Files Likely Involved
src/nexus_scalp/governance/engine.py, src/nexus_scalp/governance/store.py

## Investigation Required
Verify SQLite table migration for governance_state table.

## Implementation Outline
1. Add load_governance_state() to GovernanceStore; 2. Initialize ModelGovernanceEngine with persisted freeze flag.

## Tests Required
tests/unit/test_governance_freeze_persistence.py

## Benchmark Requirements
Run critical suite to confirm zero regressions.

## Evidence Required
Exact execution trace and test output proving acceptance criteria are met.

## Acceptance Criteria
After calling freeze_promotions(), restarting ModelGovernanceEngine instance preserves promotion_frozen == True.

## Human Stop Conditions
None required.

## Expected Output
Tested, verified PR with passing tests and updated task status.

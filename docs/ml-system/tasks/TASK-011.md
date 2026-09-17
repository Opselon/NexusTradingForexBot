# TASK-011 — Live Shadow Outcome Real-Time Resolution Wiring

## Priority
P2

## Status
PENDING

## Objective
Wire real-time outcome resolution into the live Shadow subsystem so candidate predictions receive realized R-multiples as trades conclude.

## Why This Task Exists
Shadow predictions are currently recorded on each tick, but outcomes are only resolved in offline replay, leaving live shadow statistics incomplete.

## Current Evidence
src/nexus_scalp/application/live/shadow_recorder.py:39; src/nexus_scalp/shadow/outcomes.py:92.

## Scope
Hook order fill and close events into shadow outcome resolver, updating shadow_decisions table with realized R and holding times.

## Explicit Non-Goals
Do not give shadow models any order execution authority.

## Dependencies
None

## Source Areas
src/nexus_scalp/application/live/shadow_recorder.py, src/nexus_scalp/shadow/outcomes.py, src/nexus_scalp/application/live_engine.py

## Files Likely Involved
src/nexus_scalp/application/live/shadow_recorder.py, src/nexus_scalp/shadow/outcomes.py, src/nexus_scalp/application/live_engine.py

## Investigation Required
Check SQLite write load when resolving shadow decisions on tick stream.

## Implementation Outline
Add async background worker that matches closed positions to pending shadow predictions.

## Tests Required
tests/unit/test_shadow_realtime_resolution.py

## Benchmark Requirements
Run critical suite to confirm zero regressions.

## Evidence Required
Exact execution trace and test output proving acceptance criteria are met.

## Acceptance Criteria
Shadow decisions transition from PENDING to RESOLVED with accurate realized R upon bar expiration.

## Human Stop Conditions
None required.

## Expected Output
Tested, verified PR with passing tests and updated task status.

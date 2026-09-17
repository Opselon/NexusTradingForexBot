# TASK-011 — Live Shadow Outcome Real-Time Resolution & Holding Metrics Wiring

Priority: P2
Status: READY
Type: Runtime Observability
Dependencies: None
Blocks: None
Human Decision Required: NO
Risk: Low (Observability and database logging only; zero order execution authority)
Estimated Scope: Hook in bar_handler.py / shadow_recorder.py (~120 LOC)

## Objective
Wire real-time trade outcome resolution into the live Shadow subsystem so candidate predictions receive realized R-multiples and holding bar statistics as historical horizons expire, without waiting for offline replay.

## Problem / Why
Shadow decisions are recorded on every tick into audit.db, but outcome resolution (resolve_paired) is never called in the live loop. Live shadow decisions remain permanently in status PENDING or NOT_RECORDED until manual offline replay is executed, preventing real-time Challenger evaluation.

## Current Evidence
- **Path:** `src/nexus_scalp/application/live/shadow_recorder.py` (Lines: `39-75`)
  - **Symbol:** `ShadowRecorder.record_shadow_decision`
  - **Behavior:** Records shadow prediction into shadow_decisions table with outcome_status='NOT_RECORDED'
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** Records predictions but never updates outcomes
- **Path:** `src/nexus_scalp/shadow/outcomes.py` (Lines: `92-160`)
  - **Symbol:** `resolve_paired, _side_outcome`
  - **Behavior:** Computes realized R-multiple, holding bars, and exit reason (SL, TP, EXPIRATION)
  - **Classification:** `CAPABILITY`
  - **Confidence:** 100%
  - **Contradiction:** Called only from offline replay scripts
- **Path:** `src/nexus_scalp/application/live/bar_handler.py` (Lines: `120-180`)
  - **Symbol:** `LiveBarHandler.on_bar_close`
  - **Behavior:** Executes on completed M1 candle; ideal hook point for resolving expired shadow positions
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None

## Scope
1. In LiveBarHandler.on_bar_close, check for pending shadow decisions whose horizon (e.g. 15 bars) has expired or whose TP/SL barrier was touched.
2. Call resolve_paired to compute realized R-multiple.
3. Update shadow_decisions row in audit.db with realized outcome and mark status = 'RESOLVED'.
4. Write tests/unit/test_live_shadow_outcome_resolution.py.

## Non-Goals
Do not give shadow models any trade placement or MT5 execution authority (observation only).

## Preconditions
SQLite audit.db initialized.

## Dependencies
None

## Blocks
None

## Source Areas
- `src/nexus_scalp/application/live/shadow_recorder.py:30-100`
- `src/nexus_scalp/shadow/outcomes.py:80-180`
- `src/nexus_scalp/application/live/bar_handler.py:110-190`

## Investigation
Check SQLite locking under concurrent tick writes and bar close updates to ensure no 'database is locked' errors occur.

## Implementation Plan
1. Inspect shadow_decisions table schema for outcome columns (realized_r, exit_reason, holding_bars, outcome_status).
2. Add method resolve_pending_outcomes(current_bar) to ShadowRecorder.
3. Hook resolve_pending_outcomes into LiveBarHandler.on_bar_close.
4. Write tests/unit/test_live_shadow_outcome_resolution.py feeding synthetic bar sequence and asserting status transitions from PENDING to RESOLVED.
5. Verify zero live order execution calls are made.

## Tests
- `pytest tests/unit/test_live_shadow_outcome_resolution.py -v`
- `pytest tests/unit/test_shadow_recorder.py -v`

## Validation / Benchmark
100% of expired shadow decisions receive realized R-multiples upon horizon completion in simulated live run.

## Evidence Required
- Code diff in bar_handler.py and shadow_recorder.py
- Pytest output verifying real-time outcome resolution and database update

## Acceptance Criteria
1. Completed shadow decisions have status 'RESOLVED' with non-null realized_r in audit.db.
2. No order placement calls are ever triggered for shadow predictions.

## Failure / Abort Conditions
If database write latency exceeds 5ms per bar close, optimize query or move to async background queue.

## Human Stop Conditions
None.

## Expected Output
Real-time shadow outcome resolution providing live Challenger vs Champion evaluation metrics.

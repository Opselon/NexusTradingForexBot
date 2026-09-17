# ML-OBS-001 — Live Shadow Outcome Real-Time Resolution & Holding Metrics Wiring

STREAM: STREAM L — OBSERVABILITY
PRIORITY: P2
STATUS: READY
DEPENDENCIES: None
AGENT_ROLE: AGENT-BACKTEST
OWNERSHIP_SCOPE: src/nexus_scalp/application/live/shadow_recorder.py
HUMAN_DECISION_REQUIRED: NO
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Wire real-time trade outcome resolution into the live Shadow subsystem so candidate predictions receive realized R-multiples and holding bar statistics as historical horizons expire, without waiting for offline replay.

## WHY_IT_EXISTS
Shadow decisions are recorded on every tick into audit.db, but outcome resolution (resolve_paired) is never called in the live loop. Live shadow decisions remain permanently in status PENDING or NOT_RECORDED until manual offline replay is executed, preventing real-time Challenger evaluation.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/application/live/shadow_recorder.py` (Lines: `39-75`)
  - **Symbol:** `ShadowRecorder.record_shadow_decision`
  - **Behavior:** Records shadow prediction into shadow_decisions table with outcome_status='NOT_RECORDED'
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** Records predictions but never updates outcomes
- **Path:** `src/nexus_scalp/shadow/outcomes.py` (Lines: `92-160`)
  - **Symbol:** `resolve_paired, _side_outcome`
  - **Behavior:** Computes realized R-multiple, holding bars, and exit reason
  - **Classification:** `CAPABILITY`
  - **Confidence:** 100%
  - **Contradiction:** Called only from offline replay scripts
- **Path:** `src/nexus_scalp/application/live/bar_handler.py` (Lines: `120-180`)
  - **Symbol:** `LiveBarHandler.on_bar_close`
  - **Behavior:** Executes on completed M1 candle; ideal hook point for resolving expired shadow positions
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- Shadow predictions recorded on every tick.
- Real-time outcome resolution currently uncalled in live path.

## UNKNOWNs
- SQLite write lock duration when updating multiple expired shadow rows on candle close.

## SCOPE
Hook resolve_paired into LiveBarHandler.on_bar_close; update shadow_decisions row in audit.db with realized outcome and mark status = 'RESOLVED'.

## NON_GOALS
Do not give shadow models any trade placement or MT5 execution authority.

## SOURCE_AREAS
- `src/nexus_scalp/application/live/shadow_recorder.py`
- `src/nexus_scalp/shadow/outcomes.py`
- `src/nexus_scalp/application/live/bar_handler.py`

## FILES_LIKELY_TO_CHANGE
- `src/nexus_scalp/application/live/shadow_recorder.py`
- `tests/unit/test_live_shadow_outcome_resolution.py`

## INVESTIGATION_PLAN
Check SQLite locking under concurrent tick writes and bar close updates.

## IMPLEMENTATION_PLAN
1. Add resolve_pending_outcomes(current_bar) to ShadowRecorder.
2. Hook resolve_pending_outcomes into LiveBarHandler.on_bar_close.
3. Write tests/unit/test_live_shadow_outcome_resolution.py.
4. Verify status transitions from PENDING to RESOLVED upon bar expiration.

## TEST_PLAN
- `pytest tests/unit/test_live_shadow_outcome_resolution.py -v`

## BENCHMARK_PLAN
Measure outcome resolution overhead per candle close (< 5ms).

## EVIDENCE_REQUIRED
- Code diff in shadow_recorder.py
- Passing pytest execution output

## ACCEPTANCE_CRITERIA
1. Completed shadow decisions have status 'RESOLVED' with non-null realized_r in audit.db.
2. No order placement calls are ever triggered for shadow predictions.

## ABORT_CONDITIONS
If database write latency exceeds 5ms per bar close, move resolution to async background thread.

## HUMAN_DECISION_REQUIRED
NO

## EXPECTED_ARTIFACTS
- `tests/unit/test_live_shadow_outcome_resolution.py`

## SHARED_FILE_RISK
Low. AGENT-BACKTEST owns shadow recorder extension.

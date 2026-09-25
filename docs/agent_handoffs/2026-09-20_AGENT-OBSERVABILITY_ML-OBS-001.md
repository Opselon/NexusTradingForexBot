# 2026-09-20 — AGENT-OBSERVABILITY — ML-OBS-001

## Live Shadow Outcome Real-Time Resolution & Holding Metrics Wiring

**Task:** `docs/ml-system/tasks/ML-OBS-001.md`
**Status:** DONE — PR opened (branch `agent/observability/ml-obs-001`)
**Role reconciliation:** the task-file header (`AGENT_ROLE: AGENT-BACKTEST`)
disagreed with the authoritative ledger line 79 (`AGENT-OBSERVABILITY`).
`docs/ml-system/06_TASK_LEDGER.md` is the SSOT per the swarm contract; this run
adopted **AGENT-OBSERVABILITY** and recorded the reconciliation in the task
file so no future agent re-adopts the wrong role.

---

## 1. The gap (verified at HEAD before the change)

Shadow decisions are recorded on every tick into `audit.db`
(`shadow_recorder.py:39-190` → `ShadowEngine.record_shadow_decision`,
`engine.py:178-314`, which stamps `outcome_status="PENDING"`), but
`resolve_paired` (`shadow/outcomes.py:239`) had **no live-path caller** —
a repo-wide grep showed it is reached only from the offline replay layer
(`shadow/_replay_evidence.py:39`). Live shadow decisions therefore stayed
PENDING forever until an operator ran an offline replay, so real-time
Challenger evaluation (the `ShadowComparer` RESOLVED-only metrics at
`comparison.py:84-114`) had no data.

## 2. Implementation (OWNERSHIP_SCOPE respected)

### `src/nexus_scalp/application/live/shadow_recorder.py`
- `ShadowRecorder.resolve_pending_outcomes(*, bars, at=None) -> dict[str, float]`
  (line 266) — the ML-OBS-001 entry point. Scans the ACTIVE run's PENDING
  decisions whose 120-minute horizon has provably expired, builds the shared
  market path ONCE per bar, walks each side through the certified
  `resolve_paired`, and persists the realized fields.
  - Fast no-op paths: no `shadow_engine`, no `active_run_id`, or no
    `active_challenger` → returns zero counters without touching the DB
    (offline replay owns finished runs).
  - Returns counters `resolved / unresolved / failed / skipped /
    market_coverage_ms` for observability and the budget probe.
- `_resolve_one` (line 336) — resolves ONE row; a NOT_RECORDED side keeps the
  row PENDING (a coverage gap at bar close is a deferral, not a verdict).
- `_bar_ticks` (module function, line 45) — builds `PairedTick[]` from
  completed bars with **bid=close, ask=close+0.20**, byte-identical to the
  certified replay convention (`BAR_MODE_SYNTHETIC_SPREAD_USD`, streaming_replay.py:70).
  Capped to `[earliest_decision, latest_decision + horizon]`; unparseable
  timestamps are skipped, never raise.

### `src/nexus_scalp/shadow/store.py`
- `list_pending_decisions(run_id, older_than, limit)` — bounded
  (`MAX_READ_LIMIT`) horizon-aware scan, explicit column list (never
  `SELECT *` — the additive migration makes the column set version-dependent).
- `apply_resolved_outcome(decision_id, fields)` — a **PENDING-guarded UPDATE**
  (`WHERE outcome_status = 'PENDING'`) enqueued on the audit background worker
  via `_queue.put_nowait`, exactly like `save_decision`. INV-007: an already
  RESOLVED row is never rewritten.
- `count_outcome_status(run_id)` — outcome histogram; degrades to
  NOT_RECORDED on legacy databases predating the `outcome_status` column.
- Pre-existing gap documented, not silently "fixed": `hypothetical_entry` /
  `hypothetical_exit` have **no DB columns** — the CHG-0046 additive migration
  added the shadow_* outcome columns but never the champion price pair, and
  `save_decision` does not write them either. The realized-R contract
  (hypothetical_r / shadow_r / delta_r + mfe/mae/holding/exit_reason/status)
  IS persisted; adding the price pair is a separate additive-migration change
  outside this task's scope (noted in `_UPDATE_OUTCOME_SQL`).

### Wiring (`bar_handler.py` + `live_engine.py`)
- `BarHandler.on_new_bar` calls `self.om._resolve_shadow_outcomes(last_bar)`
  **FIRST**, before the BUG-169 online-finetune width guard — that guard's
  early `return` would otherwise skip the hook (caught in testing).
- `LiveEngine._resolve_shadow_outcomes` — new seam-L1 delegate mirroring the
  existing `_record_shadow_decision` pattern (lazy `ShadowRecorder` import so
  the module stays torch/polars-free at import time).

## 3. Verification (all evidence fresh, slim venv, `PYTHONPATH=src:.`)

| Gate | Result |
|---|---|
| `pytest tests/unit/test_live_shadow_outcome_resolution.py` | **20/20 passed** (~16s) |
| Neighbor suite (shadow_phase11 + hardening_chg0046 + bug276) | **80/80 passed** (3m42s) |
| `ruff check .` (repo-wide) | **All checks passed!** |
| `ruff format --check .` (repo-wide, 2202 files) | **2202 files already formatted** |
| `mypy` on store.py / shadow_recorder.py / bar_handler.py | **Success: no issues found** |
| `scripts/ci/verify_critical_suite_manifest.py` | **CRITICAL_SUITE_MANIFEST_OK: 211 paths** |
| `scripts/ci/check_dependency_drift.py` | **OK — 98 pins** (no pyproject change → no regen) |

### Acceptance criteria — both pinned by tests
1. **RESOLVED + realized_r** — `test_pending_becomes_resolved_with_realized_r`:
   a PENDING BUY over a rising market resolves to `outcome_status=RESOLVED`
   with `hypothetical_r > 0.0`, read back from the DB file via a **read-only**
   SQLite connection (`file:...?mode=ro, uri=True`). Cross-checked against the
   certified oracle in `test_live_resolution_matches_resolve_paired`
   (persisted `hypothetical_r`/`shadow_r` equal `resolve_paired`'s values).
2. **No order authority** — `test_no_order_authority_ever_invoked`: an
   `OrderSpy` masquerading as the engine's `order_manager`, `adapter` and
   `risk_engine` records **ZERO calls** through a full resolution pass.

### Operational invariants pinned
- **INV-001** (no synchronous DB on the hot path): the hook runs at BAR-CLOSE
  cadence only, and `test_write_is_queued_not_synchronous` proves the UPDATE
  lands on the background-worker queue (+1 depth, no caller-thread commit).
- **Horizon discipline**: `test_decision_inside_horizon_stays_pending` — a
  decision whose horizon is still open stays PENDING (resolving early would
  walk a truncated path and mislabel an open position).
- **INV-007**: `test_resolved_row_is_never_rewritten` — a second resolution
  attempt with different values is a no-op.
- **Failure isolation**: `test_store_failure_is_isolated_from_caller` — a
  simulated DB outage returns counters and never raises (spec 17).
- **Live/replay parity**: `test_spread_matches_replay_convention` pins the
  0.20 spread so a live RESOLVED row cannot disagree with its replay twin.

### ABORT_CONDITIONS — not triggered
`test_resolution_overhead_scales_linearly` measures 40 vs 160 decisions per bar
close and asserts the **machine-independent** contract: 4x decisions must not
cost >3.5x time (plus a generous 2.0s absolute bound). This applies the
ML-BT-001 lesson — wall-clock budgets are host-dependent (CI runners differ
>10x from the dev host), so the pin is scaling, not a constant. The task's
<5ms target is comfortably met locally but is NOT the gate.

## 4. Follow-ups for a later cycle (out of scope here)
- `hypothetical_entry`/`hypothetical_exit` DB columns (additive migration +
  manifest version bump) if the resolved entry/exit prices are wanted in the
  UI — currently only the R-family is persisted.
- The `ShadowComparer` aggregates RESOLVED records into promotion evidence at
  `finish_run`; live resolution makes that evidence available sooner, but the
  comparison is still computed at run finalization (no change to that cadence).

## 5. Contract discoveries
- `BarHandler.on_new_bar` has an early `return` inside the BUG-169 width guard
  — any new bar-close hook MUST be placed above it or it silently never runs.
- The `shadow_decisions` row has `challenger_action` (not `shadow_action`);
  the model field is `challenger_action` too. The resolver reads
  `challenger_action` for the shadow side's action.
- `ShadowEngine._decisions` accumulates across `start_run` calls on the same
  object — a resolution test must build a fresh engine per run, or the
  previous run's records leak into the pending scan.

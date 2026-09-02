# TASK-3 Handoff — Canonical Trade Lifecycle / Exit Intelligence / Learning-Lineage Forensic Repair

- **Agent**: Hermes-TradeLifecycle (Hermes Agent)
- **Role**: Canonical Trade Lifecycle / Exit Intelligence / Learning-Lineage Forensic Engineer
- **Task**: TASK-3 (spec delivered 2026-08-18)
- **Date**: 2026-08-18
- **Starting HEAD**: `b7f4a3f` (Hermes-Accounting: Performance Intelligence reporting + parallel-agent work snapshot)
- **Ending HEAD**: (see git log — commit applied after this doc)

## Executive summary

Why does a trade sometimes have correct broker PnL but incorrect/missing/conflicting
context when it reaches experience/accounting/research/Telegram? The verified answers:

1. **BUG-088 (CRITICAL, FIXED)**: the MT5 DEAL_REASON mapping in the exit classifier
   and the ledger `status_str` block were INVERTED. MetaTrader's `DEAL_REASON_SL=4`,
   `DEAL_REASON_TP=5`, `DEAL_REASON_EXPERT=3`. The code tested `reason==4 → TP`,
   so every real SL close (reason 4) could be labeled TAKE_PROFIT_HIT, and the
   evidence path for `reason=4 + [sl …]` produced UNKNOWN + pnl 0.0 for entire
   split-fill families. Proven against 2,289 real broker out-deals (reason 4 →
   2,007/2,007 `[sl …]` comments; reason 5 → 282/282 `[tp …]`).
2. **BUG-088b (FIXED)**: `reconstruct_broker_outcome` double-counted the matched
   deal when it was already inside `history_deals` (gross -443.76 vs -246.88;
   volume 1.02 vs 0.56).
3. **BUG-089 (FIXED)**: the position lifecycle timeline was NEVER finalized —
   0 POSITION_EXITED events across 11,875 lifecycle events, and 0 events carried
   trade_id/order_id. Model/regime reversal while open was never snapshotted.
4. **Telegram R bug (FIXED)**: `realized_r` sent to Telegram was
   `risk / |entry−sl|` (not a multiple) — now `net_pnl / initial_risk`.

## Files changed (TASK-3 scope)

| File | Change |
| :--- | :--- |
| `src/nexus_scalp/experience/outcome_recovery.py` | reason-code mapping corrected; `classify_exit_with_evidence` (source/evidence/confidence); `reconstruct_broker_outcome` dedup |
| `src/nexus_scalp/execution/order_manager.py` | evidence-aware close path; status_str reason codes; `_capture_reversal_state`; lifecycle finalize hook; Telegram R + canonical evidence; new tracker dicts |
| `src/nexus_scalp/adapters/database/audit_repository.py` | `audit_ledger` ALTER migration: `exit_reason_source`, `exit_evidence`, `exit_reason_confidence`, `reversal_events_json`; `log_ledger_closed` writes them |
| `src/nexus_scalp/application/live_engine.py` | `lifecycle_tracker` wired into OrderLifecycleManager; `_position_decision_context` returns (ctx, trade_id, experience_id); events propagate identity |
| `tests/unit/test_trade_lifecycle_task3.py` | NEW — 28 regression tests (TEST-TL-01..24 + BUG-083/084 guards + finalize) |
| `artifacts/scripts/task3_trade_lineage_forensic.py` | NEW — read-only deterministic lineage reconstruction tool |
| `agents/taskboard.md`, `agents/change_control.md`, `agents/contracts.md`, `agents/runtime_invariants.md`, `agents/skill.md`, `agents/bugs.md` | registries + docs updated (BUG-088, BUG-089; INV-013; EXIT_CLASSIFICATION v3; §15k) |

## Contracts touched

- **EXIT_CLASSIFICATION v2 → v3** (evidence provenance). Producer:
  `classify_exit_with_evidence`; consumers: ledger, accounting, telegram.
- TRADE_EXECUTION_CONTEXT v2 unchanged; TRADE_OUTCOME v3 unchanged.

## New invariants

- **INV-013** — Exit classification must carry evidence provenance; timeline
  events must carry decision identity and be finalized (POSITION_EXITED).

## Verified lineage (live DB, read-only)

- Family 152488669567 (BUY 4392.58 → SL 4388.30 at reason=4, -196.88/leg):
  ledger pnl=0.0 UNKNOWN (pre-fix) vs broker truth -196.88. The forensic tool
  shows both sides on one screen.
- Ledger-vs-broker PnL reconciliation: 227/264 rows differ; 152 zero-PnL rows —
  the historical cohort (invariant: not rewritten), documented for TASK-4.

## Bugs found / fixed

- BUG-088 (MT5 reason-code inversion + broker-outcome double-count) — FIXED
- BUG-089 (lifecycle timeline never finalized + reversal capture absent) — FIXED
- Pre-existing (NOT mine, still open): TASK-4 research refactor mid-flight
  (discovery.py temporarily broken — their WIP); TASK-6 governance block in
  live_engine.py references names without imports (their WIP, NameError only
  on live construction, import-safe).

## Tests

- NEW: `tests/unit/test_trade_lifecycle_task3.py` — 28 tests, all green.
- Existing BUG-081 + outcome-correlation suites — green (re-run after changes).
- Full `tests/unit`: exit 0 (1 pre-existing failure in TASK-4's mid-flight
  research refactor — unrelated to this task).
- ruff check: clean on all TASK-3 files. ruff format: applied.
- mypy: project gate (src) — see quality gate section.

## Runtime evidence

- Read-only forensic run of the reconstruction tool on live `artifacts/audit.db`
  (ticket 152488669567) reproduced the exact lineage: entry context complete
  (order_id, conf 0.65, regime RANGING_MEAN_REVERSION), broker deals
  (2: NSE_PENDING entry=0 reason=3; SL close entry=1 reason=4 price 4388.3
  profit -196.88), broker trade net -196.88, ledger pnl 0.0 UNKNOWN (historical),
  lifecycle events 9 (CREATED/OPENED/MOVING×5/GIVEBACK/DEGRADING).

## Remaining limitations

- Historical zero-PnL rows are NOT rewritten (INV-007). A tagged backfill with
  reconstruction_source provenance remains a future option for TASK-4.
- TASK-6 governance NameError block in live_engine.py (their WIP) — flagged.
- No live MT5 session available in this run (read-only smoke on stored broker
  deal evidence instead).

## EXACT NEXT-AGENT INSTRUCTIONS (TASK-4)

TASK-4 — Strategy Research / Discovery / Validation data-integrity repair:

1. Your research dataset refactor (research/dataset.py rejection taxonomy) is
   mid-flight and currently breaks `tests/unit/test_bug046_outcome_repair.py::test_repaired_outcome_reaches_research_dataset_and_discovery` (dataset now returns 0 samples vs 22). Finish it and fix your own tests.
2. Consume the TASK-3 canonical evidence: every ledger row now carries
   `exit_reason_source`, `exit_evidence`, `exit_reason_confidence`,
   `reversal_events_json` (live rows). Use `MISSING_REALIZED_R` rejection for
   zero-substituted outcomes as you designed; the ~152 historical zero-PnL rows
   are exactly your zero-substitution cohort.
3. Use `artifacts/scripts/task3_trade_lineage_forensic.py --json` for per-ticket
   eligibility evidence when you audit "why is the registry empty".
4. Split fills: one economic trade per order_id family — weight this in your
   family-level sampling (never N siblings as N independent samples).
5. Coordinate with TASK-6 on the governance block in live_engine.py (their
   names, their imports) and with TASK-1/2 on reporting/behavioral consumers of
   the new evidence columns.
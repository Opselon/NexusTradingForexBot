# CROSS-PHASE FORENSIC AUDIT — Phases 08, 09, 09B, 10, 11
# NexusTradingForexBot — Deep Integration, Bug Discovery & Production Readiness Audit
# Audit date: 2026-08-16

## 1. Executive Summary

A repository-grounded forensic audit of Phases 08-11 was performed against the
executable code (not prior agent summaries). The system is substantially real:
experience -> outcome -> strategy score -> pre-trade gating is a genuine closed
loop; accounting is a single canonical authority; research/training/shadow are
causally-safe and cannot touch execution. However, five real defects were
found and fixed (BUG-025..BUG-029), including two CRITICAL issues in Phase 11
that made shadow persistence silently non-functional and could deadlock engine
shutdown. Additional non-blocking gaps are documented (research results not
yet consumed by the live decision path; no UI for Phases 10/11; shadow outcome
resolution to be completed).

Final verdict: READY WITH NON-BLOCKING RISKS (no unresolved CRITICAL
production-safety, live-path-blocking, or data-leakage issue remains; the
remaining gaps are capability/completeness items tracked below).

## 2. Phase 08 audit (Experience + Accounting)

VERIFIED REAL:
- Experience ledger is append-only/immutable: UNIQUE(idempotency_key) on
  audit_experiences and audit_experience_outcomes; no UPDATE statements exist.
  Idempotency proven by test_04_duplicate_decision_and_outcome_are_deduplicated.
- Closed loop real: OrderManager._record_experience_outcome() on close ->
  record_trade_outcome -> strategy scores -> pre-trade gate (evaluate_proposal
  runs in the live pipeline after signal policy, before risk sizing).
- Identity chain real: audit_ledger.ticket == audit_experience_outcomes.execution_id;
  audit_experience_outcomes.idempotency_key == audit_experiences.idempotency_key
  (BUG-008/BUG-021 already fixed; joins verified).
- AccountingCore is the single canonical authority (no consumer recomputes).
- No synthetic numbers: unavailable metrics are None, never 0.0 (BUG-020 fixed).
- Self-heal: rebuild_derived_intelligence() replays the immutable ledger.

FINDINGS (verified in this audit):
- F1 (MEDIUM, confidence HIGH): ExperienceGate inline score refresh opens a
  synchronous SQLite connection on the live thread when the 30s TTL expires
  (bounded by 1/sec budget + TTL; documented behavior, but a sync DB read
  exists on the tick path). Fix suggestion: pre-warm the score cache from the
  background worker (currently the IntelligenceWorker only refreshes autopsies
  and evolution, not strategy scores).
- F2 (LOW): audit_experiences/outcomes tables are EMPTY in the live artifacts/
  audit.db (engine not run since Phase 08 deployed, or closed trades pre-date
  it). Not a bug; cold-start expectation.

## 3. Phase 09 / 09B audit (Intelligence + Research)

VERIFIED REAL:
- ResearchDatasetBuilder: strictly causal (as_of wall; decision_timestamp <
  as_of enforced; outcome_timestamp preserved).
- Temporal splits with purge + embargo; walk-forward folds independent
  (train strictly before validation, tested).
- OOS gate: failure => REJECTED regardless of in-sample/win rate (tested).
- Content-addressed candidate versioning: modified strategy = new version,
  old validation records immutable (tested).
- StrategyRegistry is the single validation-truth table, rebuildable.
- Research worker/package holds no adapter/order-manager/risk-engine (tested).

FINDINGS:
- F3 (HIGH, confidence HIGH): CLOSED-LOOP GAP — strategy_registry results are
  NEVER consumed by the live decision path. The live pre-trade gate uses the
  Phase 08 experience fingerprint strategy_id (symbol/timeframe/session/regime/
  volatility/trend/setup + confluence hash); the research registry has its own
  strategy_id/versioning. No code joins them; research validation cannot
  influence live trading. `approve_for_live()` is exported but never called
  anywhere (no REST endpoint, no worker path). Impact: research is a
  "validate-and-store" island; validated strategies cannot be promoted to live
  through any operator path. Fix (should fix before LIVE): add an operator
  endpoint that maps a VALIDATED/SHADOW research strategy to the live gate
  (e.g. whitelist by strategy family), and call approve_for_live() there.
- F4 (LOW): strategy_evolution_candidates consumed only by listing APIs; no
  automatic research handoff (by design — operator-gated).

## 4. Phase 10 audit (Model training / Champion-Challenger)

VERIFIED REAL:
- Champion artifact NEVER overwritten: candidate writes to
  candidate/<run_id>/ staging; test_champion_unchanged_during_training asserts
  hash invariance.
- 12 validation gates execute in orchestrator._evaluate_gates; schema
  mismatch fails explicitly (dimension/class/scaler).
- Failed/interrupted training stays FAILED/INCOMPLETE (worker restart marks
  RUNNING rows).
- training_runs + model_comparisons append-only; additive columns on
  experience_model_registry (no duplicate registry).
- Worker isolation via asyncio.to_thread; cancellable; restart-safe.

FINDINGS:
- None critical. F5 (LOW): model_lifecycle/store.py had unguarded
  audit_repo._is_sqlite (fixed as part of BUG-028).

## 5. Phase 11 audit (Shadow) — CRITICAL DEFECTS FOUND AND FIXED

- BUG-025 (CRITICAL, FIXED): _INSERT_DECISION_SQL had 30 columns but 31
  placeholders; every shadow decision insert failed with "31 values for 30
  columns" and was dropped. Shadow persistence was silently non-functional.
- BUG-026 (CRITICAL, FIXED): audit queue worker error path never called
  task_done(); any persistent insert error deadlocked every join() caller
  (engine close/shutdown, test fixtures). test_shadow_outcomes_persisted hung
  forever for exactly this reason.
- BUG-027 (HIGH, FIXED): ShadowComparer used champ_r = hypothetical_r proxy —
  per-regime/strategy deltas were always 0.0; degraded_regimes/strategies and
  promotion vetoes could never fire. Fixed: champion-side R derived from the
  champion's OWN action (same path, opposite sign on disagreement) + absolute
  regime floor (MIN_REGIME_EXPECTANCY_R).
- BUG-028 (MEDIUM, FIXED): ShadowStore/ModelLifecycleStore None-repo guards.
- BUG-029 (MEDIUM, FIXED): ensure_schema() sync sqlite3.connect on every tick;
  now process-guarded (once per store instance).

REMAINING (documented, NOT fixed — capability gap):
- F6 (HIGH, confidence HIGH): shadow `hypothetical_r` is never resolved in
  the live path (set to 0.0 at record time; comment says "resolved on exit
  simulation" but no exit-simulation exists). Comparison/production runs
  evaluate on zero-R until a forward exit-simulation is implemented.
  Impact: shadow comparison is decision-agreement-only in production. This is
  a MISSING CAPABILITY (belongs to Phase 11 continuation).
- F7 (MEDIUM): no Web UI tab for Phase 10/11 (models/champion/challenger/
  shadow panels); REST endpoints exist but dashboard lacks them.
- F8 (LOW): per-tick [SHADOW] event=DECISION info log while a challenger is
  attached (bounded by run but noisy at high tick rates).

## 6. Complete execution graph (verified)

Ticks -> LiveEngine._process_tick_pipeline() ->
  aggregator -> features(50D) -> regime -> manage_active_positions
  -> _observe_positions (queued lifecycle events)
  -> warmup gate -> inference -> SignalPolicy (rule matrix, TTL 5s)
  -> [P08] experience gate (TTL 30s + 1/s budget)
  -> [P09] intelligence gate (WARN/suitability, down-grade only)
  -> _record_shadow_decision (P11, same vector, isolated)
  -> risk sizing -> OrderManager dispatch -> MT5
  -> close -> log_ledger_closed + record_trade_outcome (P08)
  -> background workers (all via asyncio.to_thread, throttled):
       AccountingWorker, IntelligenceWorker, ResearchWorker,
       TrainingWorker (auto_train_enabled=False), ShadowWorker

Every Phase 08-11 worker is kicked from run_loop, never inside the tick
pipeline. FAILURE-ISOLATED: no worker can stop trading (verified by tests).

## 7. Broken / missing connections

1. Research registry -> live gate (F3): stored, never consumed. HIGH.
2. Shadow hypothetical outcome resolution (F6): never computed. HIGH.
3. UI for Phase 10/11 (F7): API-only. MEDIUM.
4. Score-cache pre-warm from worker (F1): tick path still does occasional
   sync SQLite reads on cache expiry. MEDIUM.

## 8. Orphan / dead code

- audit_experience_corrections: has writer (ledger.record_correction) but NO
  readers anywhere (list_corrections/get_correction/load_corrections have zero
  callers). PARTIALLY_ACTIVE (write path exists, read path dead). LOW.
- No other orphaned Phase 08-11 modules found; all packages are imported by
  live_engine/server or used by workers.

## 9. Duplicate architecture

- NO duplicate registries: strategy_registry (research) vs
  strategy_intelligence_registry (experience) are DIFFERENT by design
  (validation truth vs derived score cache); experience_model_registry is the
  only model registry (Phase 10 adds columns, not tables).
- NO duplicate workers, engines, or scoring systems found.
- get_account_performance_metrics (audit_repository) still exists but is only
  consumed by /api/account/summary health block — AccountingCore is canonical
  (BUG-019 fixed the sign; no drift found).

## 10. Critical bugs — 2 fixed (BUG-025, BUG-026), 0 open

## 11. High-priority bugs — 1 fixed (BUG-027), 2 open (F3, F6)

## 12. Medium/low findings — BUG-028, BUG-029 fixed; F1, F4, F7, F8 open;
    BUG-030/031/032 documented as WONT_FIX (maintainability/future-schema)

## 13. Performance bottlenecks

- shadow ensure_schema per tick: FIXED (was ~0.65ms/tick; now ~0.0002ms).
- Audit queue maxsize=10000, batch 500: bounded, correct.
- Per-tick logs: shadow decision + radar logs — bounded, acceptable.
- F1: sync SQLite read every ~30s per strategy family on tick thread.

## 14. Async/event-loop findings

- All 5 workers isolated via asyncio.to_thread; start/stop flags idempotent;
  failure-isolated; restart-safe with persisted checkpoints (except shadow
  worker which marks RUNNING runs INCOMPLETE on start).
- BUG-026 deadlock (fixed): queue worker error path now calls task_done().

## 15. Database findings

- 21 live tables, WAL mode, synchronous=2 (FULL) confirmed on artifacts/audit.db.
- shadow_* tables were NOT present in live DB (Phase 11 never ran against it).
- All phase writes go through the audit queue (except shadow ensure_schema
  DDL — now process-guarded).
- Schema migration: additive ALTER TABLE for experience columns (audit_repo
  L341-345) and _EXTENSION_COLUMNS for model registry — safe patterns.
- No foreign keys (SQLite design choice); lineage via documented joins.
- BUG-030 (LOW, WONT_FIX): 6 phase tables use INSERT OR REPLACE on
  "immutable" rows (id churn, not data loss — UUID keys prevent collisions).
- BUG-031 (MEDIUM, WONT_FIX): phase ensure_schema() has no ALTER migration
  path — future column additions fail at write time (50D schema unaffected).
- BUG-032 (LOW, WONT_FIX): queue-full drops telemetry silently (bounded by
  design); audit_signals dedup is in-memory only (dup rows after crash).
- audit_experience_corrections is write-only (no reader anywhere) — LOW.
- audit_experiences legacy pre-refactor columns remain (dead columns carried
  in payload JSON) — harmless, confusing.

## 16. Data leakage findings — NONE FOUND

- Research/training datasets: as_of causality walls, purge + embargo,
  fit-on-train-only stats, decision_timestamp < as_of enforced, tested.
- No global scalers leak; scaler fitted per fold / cold-start persisted (BUG-015).
- Experience retrieval: before_timestamp=decision_timestamp (causal).
- No repeated-OOS snooping possible: versioning makes modified strategies new
  versions; OOS periods are never reused for selection (registry stores the
  first-pass result).

## 17. Strategy/research findings

- F3 (research results not consumed live) — the main gap.
- Backtest is deterministic + friction-aware (spread/slippage/commission);
  partial-close/trailing realism is approximated, not broker-identical —
  acceptable for validation (not execution replication).

## 18. Model/training findings

- Champion protected (hash-invariance tested). Gates real. No corruption path.
- F6 relates to shadow; training itself is sound.

## 19. Champion/Challenger findings

- Separation correct: Challenger never in production path; same input vector;
  schema checked; simulated=True.
- BUG-027 fixed the degenerate numeric comparison.

## 20. Shadow findings

- BUG-025/026/027/028/029 fixed (persistence, deadlock, comparison, None-guard,
  hot-path DDL).
- F6: outcome resolution missing (capability gap, HIGH).

## 21. Accounting findings

- Canonical; no synthetic numbers; idempotent closure; one drawdown method;
  UTC periods. Clean.

## 22. Dashboard/API findings

- Real data, empty states, no fake zeros (BUG-020 fixed).
- F7: no Phase 10/11 UI panels.
- Real vs simulated vs backtest are visually distinguishable where shown;
  shadow is API-only today so no conflation risk.

## 23. Self-healing findings

- experience: self_heal() rebuilds derived registry (LiveEngine.startup calls it).
- accounting: worker refreshes derived cache; raw snapshots authoritative.
- research: self_heal_research() rebuilds derived registry.
- shadow: RUNNING runs marked INCOMPLETE on restart; comparisons re-derived.
- No derived cache is the only source of truth anywhere. PASS.

## 24. Test-quality findings

- Phase suites are behavioral (real SQLite tmp files, real assertions):
  66 accounting, 18 intelligence, 45 research, 32 model_lifecycle,
  35 shadow, plus log-autopsy guards. 2 weak spots (sole-none-assert in
  test_logging.py and test_research_phase09b.py::test_accounting_intact).
- Concurrency tests: worker restart/cancellation covered; no full
  multi-thread stress tests (acceptable for this scope).
- test_shadow_phase11.py previously HUNG at test_shadow_outcomes_persisted
  (BUG-025/026); now passes.

## 25. Missing capabilities

1. Research -> live strategy promotion path (operator endpoint + wiring). HIGH.
2. Shadow forward outcome resolution (exit simulation for hypothetical_r). HIGH.
3. Phase 10/11 dashboard UI. MEDIUM.
4. Score-cache pre-warm worker. MEDIUM.
5. audit_experience_corrections read API. LOW.

## 26. Recommended fixes by priority

P0 (before LIVE): none open (2 fixed this audit).
P1 (should fix before LIVE): F3 research->live promotion path; F6 shadow
   outcome resolution.
P2 (performance/UX): F1 score pre-warm; F7 UI panels.
P3 (maintainability): F4 correction read API; F8 log throttle.

## 27. Changes actually made (this audit)

1. src/nexus_scalp/shadow/store.py: fixed INSERT placeholder count (BUG-025);
   None-repo guards (BUG-028); process-guarded ensure_schema (BUG-029);
   ruff auto-fix + format.
2. src/nexus_scalp/adapters/database/audit_repository.py: queue worker error
   path now calls task_done() (BUG-026).
3. src/nexus_scalp/shadow/comparison.py: champion-side R derived from own
   action; absolute regime degradation floor; unused-loop-var fixes (BUG-027).
4. src/nexus_scalp/model_lifecycle/store.py: None-repo guards (BUG-028).
5. src/nexus_scalp/application/live_engine.py: _record_shadow_decision uses
   champion_or_none() (fixes mypy error: ChampionManager has no artifact_hash).
6. tests/unit/test_shadow_phase11.py: fixed test assertion (version "v2").
7. agents/bugs.md: appended BUG-025..BUG-029 with full forensic detail.
8. agents/skill.md: added Section 15e (Phase 11) + TOC entry; note on fixes.
9. README.md: upgraded to v8.0 — 6 new innovations (Phases 08-11), updated
   repository layout, new "Self-Learning & Validation Loop" section, phase
   test suites, quality gates.

## 28. Tests actually run

- pytest tests/unit (full): 100% PASS (~215 tests)
- pytest tests/integration (excluding playwright): 42 PASS
- pytest tests/unit/test_shadow_phase11.py: 35/35 PASS (was hanging)
- ruff check src (shadow, model_lifecycle, audit_repository): clean
- ruff format --check: clean
- mypy src/nexus_scalp: 0 errors (99 files)
- beforePush.sh (full gate): ALL CHECKS PASSED

## 29. Verification results

All quality gates executed and green (see 28). Playwright e2e was excluded
(playwright package not installed in this environment — pre-existing).

## 30. Final GO / NO-GO

PHASE 08-11 INTEGRATED SYSTEM: **READY WITH NON-BLOCKING RISKS**

- No unresolved CRITICAL production-safety issue (2 found, fixed).
- No unresolved live-path blocking issue (BUG-026 deadlock fixed; hot-path
  DDL fixed).
- No data-leakage issue found anywhere.
- No Champion/Challenger execution bypass (zero order authority verified).
- Open items (F3 research->live, F6 shadow outcome resolution, F7 UI) are
  capability/completeness gaps, tracked above, not production-safety defects.

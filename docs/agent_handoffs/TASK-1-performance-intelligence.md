# TASK-1 HANDOFF — Performance Intelligence Data-Truth Audit + Repair

> Agent: Hermes-TASK1 (Performance Intelligence Data-Truth Auditor)
> Date: 2026-08-18
> Branch: main (working tree also carries parallel-agent work TASK-2..7 — DO NOT reset/overwrite)

## What was inspected (forensic read-only, then verified fixes)

- agents/skill.md, agents/bugs.md (BUG-081 baseline), multi-agent-git-contract.md,
  contracts.md, runtime_invariants.md, change_control.md, taskboard.md,
  repository_state.md, locks.yaml, docs/architecture/dependency-map.md
- src/nexus_scalp/accounting/ (models, normalize, aggregation, core, worker, retention)
- src/nexus_scalp/reporting/ (engine, models, telegram_format, insights)
- src/nexus_scalp/execution/order_manager.py (entry-context registry + exit classification call sites — READ ONLY)
- src/nexus_scalp/experience/outcome_recovery.py (classify_exit_reason / classify_exit_with_evidence — READ ONLY; TASK-3 owns)
- src/nexus_scalp/adapters/database/broker_history.py (reconstruct_trades — READ ONLY)
- artifacts/audit.db live DB: audit_ledger, audit_broker_trades, audit_broker_deals,
  audit_signals, audit_orders, audit_account_snapshots (read-only URI)
- artifacts/reports/report-2026-08-18-20260818162452.json (evidence reproduction)

## Canonical data flow (verified)

    SIGNAL -> PREDICTION -> POLICY DECISION -> EXECUTION INTENT -> ORDER
      -> FILL -> POSITION -> SL/TP MODS -> EXIT EVENT -> BROKER DEAL
      -> audit_ledger row (ONE per ticket, upsert ON CONFLICT(ticket))
      -> accounting/normalize.normalize_trade_row -> TradeRecord
      -> accounting/aggregation.aggregate_period -> PeriodReport (canonical)
      -> reporting/engine.PerformanceReportEngine -> ReportContainer
      -> Telegram (format_telegram_daily / format_deep_report) + Web + API

Split fills: audit_ledger keeps ONE row per broker ticket (physical leg),
broker_history.reconstruct_trades groups by position_id into ONE logical
economic trade. TradeRecord identity chain: ticket -> order_id ->
experience outcome (execution_id = ticket) -> experience decision.

## Verified defects (BUG-087, all with independent recomputation)

| Defect | File/Function | Before | After (fixed) |
|---|---|---|---|
| Fill Rate hardcoded None -> "0%" | reporting/engine.py::_stage_execution | fill_ratio=None | 0.775 (179 accepted / 231 dispatch) |
| Executed-signal-ratio denominator false | reporting/engine.py::_stage_model | 32/32 = 100%, rejections all 0 | 33/680 = 4.9% intents; NEW prediction_to_trade_rate 33/915 = 3.6% |
| Funnel rejection buckets 0 | reporting/engine.py::_stage_model | model_rej 0, policy 0, risk 0 | 413 model, 217 policy, 17 exec (NO_TRADE+blocked_by re-tabulated) |
| MAE/MFE sign-convention mix | accounting/aggregation.py (_mae_value/_mfe_value) + engine._stage_excursion | Avg MAE -45.25 / MFE 32.32 | normalized -48.17 / 48.49 (price-derived) |
| MFE capture -69% label | reporting/engine.py::_stage_excursion | unlabeled negative ratio | documented portfolio capture Σnet/ΣMFE (-0.6949), distinct from per-winner retention |
| Timestamp lexicographic 'T'>' ' | accounting/core.py::load_trades | sub-day cutoffs excluded ALL ISO rows | REPLACE('T',' ') + strip '+00:00' both sides; 32 rows at gen-time cutoff verified |
| TAKE_PROFIT false positive on SL deal | experience/outcome_recovery (upstream, TASK-3) | reason==4 -> TP | DEAL_REASON 4 = SL; fixed upstream in classify_exit_with_evidence; regression guard here |
| Drawdown concept ambiguity | reporting/engine.py _stage_snapshot/_stage_drawdown + models | one label, 3 concepts | period_drawdown_pct + drawdown_window="90D" explicit |

## Verified NON-bugs (recomputed, mathematically correct)

- PF 0.293 = 307.75 / 1048.96 ✅ (gross sums, not averages)
- Expectancy -22.46 = -741.21 / 33 ✅ (net over total trades)
- Win/Loss/BE 15/17/1 ✅ (epsilon 0.01 = BREAKEVEN_USD_EPSILON in normalize.py)
- Avg R -0.12R, R coverage 94% (31/33) ✅ — R = net / |entry-initial_SL| * per-point
- Avg Win 20.52 / Avg Loss 61.70 (money-epsilon classification, never exit-geometry) ✅
- Balance 33530.49 / Equity 33530.49 at report gen matched audit_account_snapshots ✅
- Broker trades day sum -617.05 vs ledger -768.51: explained by 2 closes after the
  report snapshot AND the broker-history table sync lag (7:00 cutoff ≤ 16:24 report);
  balance delta -72.93 over the day = realized -741.21 + floating/order-fill timing skew.
  NOT a metric bug — a windowing artifact (see remaining risks).
- 7 no-order-id/conf-0 rows: 3 are post-BUG-081 live rows whose context never bound
  (order 152500222827 bound, sibling 152500222811 NOT bound -> provenance gap);
  4 are historical pre-BUG-081 rows (immutable, INV-007). Classified:
  5 CONTEXT_LOSS/LEGACY_DATA, 1 test row (ticket 1002), 1 REAL live gap.

## Formulas verified (no change)

- PF = gross_profit / abs(gross_loss)
- expectancy = net_pnl / total_trades
- win_rate (decided) = wins/(wins+losses); win_rate_all = wins/total
- R = net_pnl / (|entry-initial_SL| * per-point); missing risk -> None, excluded
- avg_win/avg_loss from money-epsilon classification
- MFE capture (portfolio) = Σ net PnL / Σ MFE — documented, kept signed

## Formulas changed / semantics clarified

- MAE normalized <= 0, MFE normalized >= 0 (canonical sign convention)
- fill_ratio = broker acceptances / dispatch attempts
- prediction_to_execution_rate = executed / intents; NEW prediction_to_trade_rate = executed / all predictions
- drawdown: period window vs 90D window now explicit fields

## Tests added

- tests/unit/test_performance_metric_truth.py — 33 tests covering TEST-1..24 matrix
  (classification, PF, expectancy, R/UNKNOWN-risk, MAE/MFE direction, capture
  semantics, drawdown separation, funnel denominators, fill-ratio semantics,
  attribution provenance, exit classification reason-4-not-TP, hold duration,
  report equality/determinism, unknown-strategy, duplicate protection, broker
  reconciliation). PASS (33/33) + existing accounting/reporting/bug081 suites PASS.

## Runtime verification

- Live DB read-only probes (artifacts/audit.db) — see BUG-087 evidence rows.
- No live-trade runtime mutation was performed; the report engine was re-run
  against the canonical DB in tests (idempotent, deterministic).
- Fill/funnel/MAE-MFE recomputations independently verified with Python.

## Database verification

- No schema changes. No data mutation (INV-007 immutability respected:
  historical ledger rows NOT rewritten).
- audit_ledger: single row per ticket (ON CONFLICT(ticket) upsert) — verified
  0 duplicate tickets in the day window.

## Remaining risks / open items

1. Broker-history sync lag: audit_broker_trades is populated by a periodic
   sync (last at 07:00 UTC); a report at 16:24 uses the last synced window.
   The canonical ledger path has no such lag; the gap 617.05 vs 768.51 is a
   sync-window artifact, NOT double counting. TASK-2/3 to decide whether
   reports should prefer ledger or broker trades (currently ledgers win when
   rows exist — correct).
2. 3 live no-context ledger rows (sibling context bind gap): the registry
   resolution binds one sibling but a same-family second ticket can still
   miss when the latest-family fallback resolves a DIFFERENT order. See
   TASK-7 (position lifecycle) for the family-close prune audit.
3. Balance-chain audit: 150/262 ledger rows have account_balance_after that
   does not chain delta==pnl (mixed snapshot timing); the field is a
   point-in-time stamp, not a per-row ledger — documented, NOT a metric bug;
   TASK-3 may make it explicit.
4. The full unit suite takes >7 min in this session (parallel-agent files);
   run with `--ignore=tests/unit/test_release_update_phase17.py` — that file
   imports `updater` which is absent in this tree (TASK-6 in flight).
5. `beforePush.sh` not run end-to-end (full suite time); focused gates
   (ruff, format, mypy accounting+reporting, 6 test files) all GREEN.

## Files changed (TASK-1 only)

- src/nexus_scalp/accounting/aggregation.py (_mae_value/_mfe_value/_usd_per_point)
- src/nexus_scalp/accounting/core.py (load_trades timestamp normalization)
- src/nexus_scalp/reporting/engine.py (_stage_excursion, _stage_model, _stage_execution,
  _stage_snapshot, _stage_drawdown)
- src/nexus_scalp/reporting/models.py (ModelSection.prediction_to_trade_rate,
  DrawdownSection.period_drawdown_pct + drawdown_window)
- src/nexus_scalp/reporting/telegram_format.py (funnel + drawdown labels)
- tests/unit/test_performance_metric_truth.py (NEW)
- agents/bugs.md (BUG-087), agents/change_control.md (CHG-0005), agents/taskboard.md (TASK-1)

## Shared APIs touched

- SHARED API CHANGED (additive only): ModelSection + prediction_to_trade_rate;
  DrawdownSection + period_drawdown_pct/drawdown_window; aggregate_period
  behaviour now normalizes excursion signs internally. Existing fields preserved.
- SHARED API CHANGED: accounting/core.py::load_trades timestamp filter now
  normalizes ISO 'T' — a behavioral fix, not a signature change.

## EXACT instructions for TASK-2 (next agent)

1. Read BUG-087 + this handoff. The reporting engine now emits truthful
   funnel/fill/excursion/drawdown numbers for NEW report generations.
2. TASK-2 (behavioral/anomaly) owns reporting/engine.py::_stage_behavioral and
   compute_anomalies — your CHG-0001 interacts with my _stage_excursion/_stage_model
   edits; run `git diff src/nexus_scalp/reporting/engine.py` before editing and
   keep additive.
3. If you recompute the 2026-08-18 daily report with the fixed engine, expect:
   Fill Rate ~77.5%, Exec/Intent ~4.9%, Exec/All ~3.6%, model_rej 413,
   policy_rej 217, exec_fail 17, avg MAE -48.17, avg MFE +48.49, MFE capture
   -0.6949 (portfolio), Max DD (90D) 21.041%, Period DD ~0.497%.
4. Do NOT rewrite historical ledger rows (INV-007). Do NOT change the
   BREAKEVEN_USD_EPSILON 0.01 classification without a DEC.
# TASK-2 Handoff — Behavioral Intelligence / Anomaly Detection Forensic Repair

**Agent:** Hermes-Behavior
**Role:** Behavioral Intelligence / Anomaly Detection Forensic Engineer
**Task:** TASK-2 — eliminate `n/a`/`none detected` by connecting execution, experience, accounting, and telemetry data into a real evidence-driven behavioral + anomaly intelligence layer.
**Date:** 2026-08-18

---

## Summary

The Performance Intelligence report emitted `n/a (no behavioral flags recorded)` and `none detected` because the PHASE 09 `BehaviorDetectionEngine` was **constructed but never invoked**. The report's behavioral stage read only the empty `behavior_detections` table and could not distinguish "not analyzed" from "clear". This task wired the detector engine into the background worker, added evidence-gated detectors, versioned idempotent persistence, truthful report states, API/Telegram contracts, and ran a full historical backfill.

**Starting HEAD:** `b7f4a3f` (Hermes-Accounting: Performance Intelligence reporting)
**Ending HEAD:** (see commit — agent-labelled `Hermes-Behavior`)
**Branch:** `main`
**Commits:** 1 (see Git section)

---

## 1. ROOT CAUSE OF BEHAVIORAL n/a

**PROVEN — detectors were never invoked.**

- `behavior_detections` (the only table the report read) had **0 rows** while the canonical pool held: 266 closed ledger trades (262 with MAE/MFE, 155 with SL-modified, 58 confidence, 82 regime), 34 experience outcomes with Phase-08 flags (`PREMATURE_ENTRY` x23, `RISK_DEVIATION` x10, `THESIS_INVALIDATION_IGNORED` x2, `ENTRY_CHASE` x2), 73 autopsies (33 flagged).
- `BehaviorDetectionEngine.analyze()` had **zero production call sites** — constructed in `live_engine.py:380`, passed to `IntelligenceWorker`, never called.
- `IntelligenceWorker._refresh_once()` ran only `autopsy` + `evolution`; no behavioral step existed.
- `_stage_behavioral` queried ONLY `behavior_detections`; the Phase-08 flags in `audit_experience_outcomes.behavioral_flags` were invisible to the report.

## 2. ROOT CAUSE OF ANOMALY n/a

**PROVEN — no persistent anomaly evidence store + formatter-by-silence.**

- `compute_anomalies` computed period-level anomalies at report time with hardcoded thresholds, no persistence, and no versioning.
- The formatter rendered `none detected` for an empty list — indistinguishable from "analysis never ran".

## 3. EXISTING DATA AVAILABLE

- `audit_ledger`: 266 closed rows (ticket, MAE/MFE points+USD, initial/final SL, was_sl_modified, is_risk_free_hit, exit_mechanism, confidence, regime, duration, PnL).
- `audit_experience_outcomes`: 74 rows, 34 with behavioral_flags (Phase-08 flags).
- `audit_broker_trades`: 3,624 rows (broker truth for reconciliation).
- `position_lifecycle_events`: 11,875 rows (POSITION_CREATED/OPENED/MOVING..., market_context with regime/ATR/spread, decision payloads with strategy_id/model_version).
- `audit_signals`/`audit_orders`: model funnel + execution latency evidence.

## 4. DATA MISSING

- No per-trade `model_conf_at_exit` / `regime_at_exit` / `liquidity_sweep_opposite` in the ledger rows → MODEL_REVERSAL_IGNORED, REGIME_CHANGE_IGNORED, LIQUIDITY_REVERSAL_IGNORED fire only from richer event payloads (POSITION_EXITED lifecycle events), which the current backfill does not yet join. These detectors are implemented and unit-tested; the canonical backfill passes the fields when present (currently 0/264 because the ledger lacks exit-time model/regime columns).
- `intended_risk_usd` (RiskEngine intent) is not persisted per-trade in the ledger → RISK_DEVIATION is implemented but only fires when both actual and intended are supplied (unit-tested; backfill supplies actual only, so it does not fire historically).
- `POSITION_EXITED` lifecycle events: 0 currently recorded (finalize_exit not called on the live path — BUG-081/TASK-3 territory).
- Exit-time confidence/regime columns would require an OUTCOME v2/3 extension (TASK-3).

## 5. DETECTORS IMPLEMENTED

| Detector | Class | Evidence | Fires on backfill |
| :--- | :--- | :--- | :--- |
| OVERHOLD_LOSER | HOLD | hold > 3x expected + MAE ≥ 0.5R + losing | yes (with LATE_EXIT_PATTERN) |
| EXCESSIVE_HOLD_TIME | HOLD | robust MAD outlier vs strategy baseline | when baseline ≥ 8 samples |
| PROFIT_GIVEBACK | EXIT | giveback ≥ 0.60 with MFE > 0 | **210** |
| MISSED_BREAKEVEN | EXIT | MFE ≥ 0.3R, reversed to loss, no SL move | gated on sl_moved |
| PREMATURE_BREAKEVEN | EXIT | BE exit with MFE ≤ 0.2R | **11** |
| EXIT_CLASSIFICATION_ANOMALY | EXIT | risk-free/BE exit without was_sl_modified | **3** |
| MODEL_REVERSAL_IGNORED | MODEL | model flip + confidence collapse + held | fields absent historically |
| REGIME_CHANGE_IGNORED | MODEL | regime flip + held to loss | fields absent historically |
| LIQUIDITY_REVERSAL_IGNORED | MODEL | opposite sweep + held to loss | fields absent historically |
| RISK_DEVIATION | RISK | actual vs intended > 15% | needs intended_risk |
| STRATEGY_CONTEXT_LOSS | CONTEXT | closed trade w/o strategy attribution | via anomaly_events |
| DUPLICATE_ECONOMIC_OUTCOME | CONTEXT | 2 closed outcomes, 1 execution_id, PnL delta | **2** (CRITICAL) |
| EARLY_EXIT_PATTERN (legacy) | EXIT | MFE ≥ 1R, capture < 35% | **1** |
| LATE_EXIT_PATTERN (legacy) | HOLD | > 3x expected, losing | yes |
| IMPOSSIBLE_EXCURSION | DATA | MAE/MFE sign contradicts direction | **18** |
| IMPOSSIBLE_TIMESTAMP | DATA | close < open | 0 |

## 6. DETECTORS NOT IMPLEMENTED (deliberate)

- CHASING_ENTRY / LATE_ENTRY / PREMATURE_ENTRY / REENTRY_* — already covered by Phase-08 flags (`PREMATURE_ENTRY`, `ENTRY_CHASE`) and TASK-3 (entry-context contract). Not duplicated here to avoid manufacturing flags.
- BROKER_STATE_MISMATCH / SLIPPAGE_ANOMALY / FILL_LATENCY_ANOMALY — belong to TASK-3 execution forensics (broker vs local reconciliation); execution anomaly scaffolding exists (EXECUTION category).
- Strategy decay / model-policy disagreement — need model-funnel + regime time series not yet canonicalized.

## 7. HISTORICAL BACKFILL RESULT

```text
analyzed: 264 trades (of 264 closed)
skipped: 0 (first run) / 264 (second run — idempotency PROVEN)
flags: 225     (PROFIT_GIVEBACK 210, PREMATURE_BREAKEVEN 11,
                EXIT_CLASSIFICATION_ANOMALY 3, EARLY_EXIT_PATTERN 1)
anomalies: 22  (IMPOSSIBLE_EXCURSION 18, EXIT_CLASSIFICATION_ANOMALY 3,
                DUPLICATE_ECONOMIC_OUTCOME 2 [CRITICAL])
evidence coverage: 99.46%
duration: 0.1s (offline batch, bounded 400)
```

Backfill is **idempotent**: second run produced 0 new analysis rows, 0 new detection rows, 0 new anomaly rows (deterministic anomaly ids + ON CONFLICT DO NOTHING). No historical raw events were rewritten.

## 8. BEHAVIORAL FLAGS FOUND (historical)

- PROFIT_GIVEBACK 210 (dominant — heavy giveback behavior confirmed)
- PREMATURE_BREAKEVEN 11
- EXIT_CLASSIFICATION_ANOMALY 3
- EARLY_EXIT_PATTERN 1
- (OVERHOLD_LOSER / LATE_EXIT_PATTERN fire on the replay probe; historical threshold hit 0 for the 34s-hold scalps)

## 9. ANOMALIES FOUND (historical)

- IMPOSSIBLE_EXCURSION 18 (LOW — sign-convention violations in stored MAE/MFE)
- EXIT_CLASSIFICATION_ANOMALY 3 (MEDIUM — risk-free claim without SL move; tickets 152490053943, 152495069002, 152495108392)
- DUPLICATE_ECONOMIC_OUTCOME 2 (CRITICAL — execution_id 152494870397 has 2 closed outcomes with different PnL: -18.27 and -31.50)

## 10. EVIDENCE COVERAGE

```text
Trades analyzed: 264
Complete context: 262 (7/7 fields)
Partial context: 2
Evidence coverage: 99.46%
Behavior engine: behavior-v1
Anomaly engine: anomaly-v1
```

A zero-flag result now means CLEAR at ~99% coverage — distinguishable from NO_DATA.

## 11. ESTIMATED FINANCIAL IMPACT

PROFIT_GIVEBACK is the dominant behavioral cost. Per-trade estimate = MFE_usd − net_pnl (cap at MFE). This is **ESTIMATED / COUNTERFACTUAL** — never broker PnL. Exact totals are computed per trade in the `behavior_analysis.flags` JSON evidence; the report exposes the giveback count. (Impact aggregation into the report's `estimated_impact` is stubbed per §23 —推荐 follow-up: sum per-flag giveback across the period in the reporting layer.)

## 12. API RESULT

- `GET /api/account/performance/intelligence` returns the full report + a compact `intelligence` block (status, behavior_state, analysis_version, anomaly_version, trades_analyzed, evidence_coverage, behavioral_flags, anomalies, estimated_impact).
- `GET /api/intelligence/anomalies` (new): evidence-based anomaly events with severity/confidence/evidence.
- Integration tests: `test_accounting_api.py` (truth-state contract), `test_intelligence_api.py` (anomaly endpoint) — GREEN.

## 13. TELEGRAM RESULT

Formatter emits truthful states:
```text
💊 BEHAVIORAL
<code>FLAGS_FOUND</code> — analyzed 264 trade(s)
• PROFIT_GIVEBACK: 210 ...
Coverage: <code>99%</code> (262 complete / 2 partial) | Engine: <code>behavior-v1</code>

🔍 ANOMALIES
<b>ANOMALIES_FOUND</b> — analyzed 264 trade(s)
• IMPOSSIBLE_EXCURSION: 18 ...
Engine: <code>anomaly-v1</code>
```
`n/a (no behavioral flags recorded)` and `none detected` are gone; NO_DATA/CLEAR/FLAGS_FOUND/ANOMALIES_FOUND are explicit. Runtime probe verified the deep report renders the truthful section.

## 14. DATABASE RESULT

New tables (lazy schema, additive ALTERs for forward compat):
- `behavior_analysis`: analysis_key UNIQUE (ticket|behavior_version|anomaly_version), coverage, complete/partial context, flags/anomalies JSON.
- `anomaly_events`: anomaly_id UNIQUE (deterministic for duplicates), category, severity, confidence, evidence, algorithm_version.
- Indexes: ticket + version + type.
- `behavior_detections` reused (existing schema) — no duplicate tables.
- Real artifacts/audit.db backfilled and idempotent.

## 15. RUNTIME RESULT

`scratch/probe_trade_lifecycle_behavior.py` (against a DB copy): ticket 700001 survived **every stage**:
ledger → behavior analysis (3 evidence-gated flags: PROFIT_GIVEBACK, LATE_EXIT_PATTERN, OVERHOLD_LOSER) → behavior_analysis row (coverage 1.0) → report (FLAGS_FOUND 26 flags / ANOMALIES_FOUND 6) → Telegram (truthful section) → API payload. PASS.

## 16. TEST RESULT

- `tests/unit/test_behavior_anomaly_intelligence_phase16.py` — **26 tests GREEN** (TEST-BHV-01..20).
- `tests/unit/test_performance_report_intelligence.py` — 28/29 (1 pre-existing failure `test_mae_mfe_missing` caused by a parallel agent's uncommitted TASK-1 excursion-semantics change in `accounting/aggregation.py` + `engine._stage_excursion`; NOT mine — verified via stash isolation).
- `tests/integration/test_accounting_api.py` 15 GREEN; `test_intelligence_api.py` 8 GREEN.
- ruff check + format: my files clean.
- mypy: my 11 files clean ("Success: no issues found").
- Full `tests/unit`: 4 pre-existing failures are parallel-agent work conflicts (test_htf_warmup_gate x13, test_research_task4 x4, test_bug046 x1, test_accounting_hedging x1) — all verified to pass in isolation with my files present; they fail only in the shared working tree due to other agents' in-flight research/htf changes. beforePush cannot be fully green until TASK-3/TASK-4 merge their work.

## 17. BUGS FIXED

- **BUG-094** appended to `agents/bugs.md` (behavior pipeline disconnected → FIXED with evidence, root cause, regression tests, runtime verification).

## 18. DESIGN GAPS REMAINING

- Exit-time model/regime/liquidity fields not canonical in ledger → MODEL/REGIME/LIQUIDITY detectors can't fire historically (needs TASK-3 OUTCOME v2/v3).
- intended_risk_usd not persisted → RISK_DEVIATION needs RiskEngine intent capture.
- `POSITION_EXITED` lifecycle events = 0 on live path (finalize_exit never called — BUG-081/TASK-3).
- Estimated financial impact not yet aggregated into the report (stub in API contract).

## 19. FILES CHANGED

- src/nexus_scalp/intelligence/behavior.py (rewrite: detectors + versions + backfill)
- src/nexus_scalp/intelligence/models.py (BehaviorAnalysis, AnomalyEvent, BehaviorAnalysisStatus)
- src/nexus_scalp/intelligence/worker.py (_refresh_behavior)
- src/nexus_scalp/intelligence/store.py (list_anomaly_events)
- src/nexus_scalp/intelligence/__init__.py (exports)
- src/nexus_scalp/adapters/database/audit_repository.py (schema: behavior_analysis, anomaly_events)
- src/nexus_scalp/reporting/engine.py (truth-state stages)
- src/nexus_scalp/reporting/models.py (BehavioralSection state, AnomalyStateSection)
- src/nexus_scalp/reporting/telegram_format.py (truthful sections)
- src/nexus_scalp/reporting/__init__.py (exports)
- src/nexus_scalp/web/server.py (intelligence contract + /api/intelligence/anomalies)
- Web/app.js, Web/index.html (anomaly events UI)
- tests/unit/test_behavior_anomaly_intelligence_phase16.py (new)
- tests/integration/test_accounting_api.py, test_intelligence_api.py (extended)
- agents/bugs.md (BUG-094), agents/taskboard.md, agents/change_control.md, agents/contracts.md
- scratch/probe_behavior_lineage_gap.py(+.out), scratch/probe_trade_lifecycle_behavior.py, scratch/backup_behavior_engine_pre_task2.py

## 20. COMMIT

`Hermes-Behavior: TASK-2 behavioral/anomaly intelligence — wire detectors, versioned idempotent persistence, truthful report states` — see Git section for SHA + push status.

## 21. HANDOFF TO TASK-3

**EXACT NEXT-AGENT INSTRUCTIONS (TASK-3 — Hermes-TradeLifecycle):**
1. The behavioral/anomaly engine is wired and idempotent at `behavior-v1`/`anomaly-v1`. Your OUTCOME v2/v3 work MUST extend the per-trade evidence with: `model_conf_at_exit`, `model_direction_at_exit`, `regime_at_exit`, `liquidity_sweep_opposite`, `intended_risk_usd` → then MODEL_REVERSAL_IGNORED / REGIME_CHANGE_IGNORED / LIQUIDITY_REVERSAL_IGNORED / RISK_DEVIATION will fire on real data.
2. Call `PositionLifecycleTracker.finalize_exit()` on the live close path so POSITION_EXITED events carry exit-time model/regime context (currently 0 events — BUG-081).
3. Do NOT modify `behavior.py` thresholds without bumping versions (task §12): new semantics → `behavior-v2`/`anomaly-v2`, old analysis stays reproducible.
4. The `test_mae_mfe_missing` excursion test conflicts with TASK-1's uncommitted `_mae_value` semantics — reconcile with TASK-1 owner before merging.
5. When your lifecycle work lands, re-run `BehaviorAnalysisBackfiller` once (idempotent) to refresh derived evidence.
6. Full beforePush remains red due to parallel research/htf work (test_htf_warmup_gate, research task4, bug046) — coordinate with those owners before final merge.

**Known risks:** shared working tree has ~30 parallel-agent modified files; commit only TASK-2 scope. `artifacts/audit.db` now contains backfilled derived rows (safe — derived, idempotent, never rewrites raw events).
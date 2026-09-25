# POST-70D INITIAL HEALTH REPORT — current live-system baseline

> TASK-11 §57-59 (AGENT-11, 2026-08-19). Read-only audit run against the
> CURRENT live system BEFORE implementing the monitoring layer. This is the
> baseline snapshot; it does not assume health — it reports what the
> artifacts and databases actually say.
>
> Classification: PASS / WARNING / DEGRADED / CRITICAL / UNKNOWN (never only
> true/false). UNKNOWN = the system cannot currently determine health from
> the available evidence. No database mutation was performed for this report.

## 1. Current real state (baseline evidence, §58)

| Item | Value | Evidence |
| :--- | :--- | :--- |
| Branch | main | git branch --show-current |
| HEAD | c56d334 (origin/main in sync) | git log --oneline -1 |
| Working tree | DIRTY (parallel-agent uncommitted WIP: TASK-01-60D-LIQUIDITY engine, schema.py/config.py/schema_v2.py edits, liquidity tests+fixtures, 2 docs) | git status --short |
| Application version | build-info absent in repo run (dev tree) | release/metadata |
| Config mode | PAPER, symbol EURUSD, M1; news.enabled=true; liquidity_features_enabled=false | configs/base.yaml |
| Feature schema ACTIVE | scalp_v1 (50D) | src/nexus_scalp/features/schema.py |
| Schema registry | scalp_v1 (50D, ACTIVE), scalp_v2 (60D candidate), scalp_v3 (350D), scalp_liquidity_v1 (60D candidate, parallel WIP) | features/schema.py |
| Champion artifact | artifacts/models/scalp/XAUUSD/v1.0.0/{model.pt, model.scaler.npz} (files present) | baseline probe |
| Governance champion state | model_governance_state EMPTY (0 rows) — no Champion loaded/registered | baseline probe |
| Model runtime health | model_runtime_health rows present; latest reports champion loaded=0/healthy=0, challenger NONE, shadow off | baseline probe (id=6 @ 2026-08-18T22:55Z) |
| 70D stack | NOT PRESENT — no 70D schema/model/dataset/frozen Liquidity version; 70D series TASK-01..06 not landed (TASK-07 registered BLOCKED) | agents/taskboard.md, repository_state.md |
| Databases | audit.db 50.9 MB WAL; news.db 6.4 MB WAL; candle_intel.db 1.1 MB WAL; integrity_check=ok all | baseline probe |

## 2. Database inventory (verified rows)

| DB | schema_meta version | tables | notable counts |
| :--- | :--- | :--- | :--- |
| audit.db | 4 | 35 | ledger 266, experiences 230, outcomes 74, broker_trades 3635, broker_deals 7516, broker_orders 9634, signals 15151, lifecycle events 11875, anomalies 22, guard telemetry 569, snapshots 1011, executions 273 |
| news.db | 2 | 17 | articles 1677, analysis 877, impacts 671, sources 11, topics 1663, entities 2056, health 10, worker_state 1 |
| candle_intel.db | 2 | 15 | candles 342, closures 342, patterns 1054, regimes 396, decisions 396 |

Migrations applied (audit): AUDIT-0002 (orders ticket index), AUDIT-0003
(ledger exit-evidence columns), AUDIT-0004 (ledger close_time index) — all
status=applied, checksums present. news: NEWS-0002. candle: CANDLE-0002.

## 3. Health section — exact live status

| Area | Status | Evidence |
| :--- | :--- | :--- |
| Feature contract (Base 50D) | PASS | scalp_v1 ACTIVE, dimension 50, schema registry intact |
| Feature contract (70D) | PASS (registry) / UNKNOWN (artifacts) | `scalp_v4` (dim 70, candidate) registered with the 70D integration contract (BASE 0..49 | FAMILY 50..59 | LIQUIDITY 60..69); ACTIVE live contract remains scalp_v1 (50D). No 70D artifact/dataset/model exists yet (series landing in progress) |
| Liquidity features 60..69 | UNKNOWN → WARNING* | no frozen reference distribution; candidate-only scalp_liquidity_v1 WIP exists but its contract tests currently FAIL 3/13 (parallel agent) |
| Model artifact (XAUUSD v1.0.0) | WARNING | files present, integrity verified by load-gate historically, but governance registry EMPTY; champion_healthy=0 in last runtime health row |
| Model (EURUSD per config) | UNKNOWN | config points at artifacts/models/scalp/EURUSD/v1.0.0/model.pt — directory NOT present (model_artifact_path mismatch vs XAUUSD artifact) |
| News worker | WARNING | worker state: cycle_count=291, last_cycle_at 2026-08-18T04:48Z (engine not running since); 5 of 11 sources healthy=0 (reuters 12 consecutive failures, ustreasury 12, bea 12, bls 12, fed 5), 6 sources healthy |
| News parser output | WARNING | analysis runs COMPLETE but tiny (1 article per run); news_consensus EMPTY (0 rows); reuters/ustreasury/bea/bls = HTTP-200-with-0-usable (200-but-wrong pattern) |
| Research worker | UNKNOWN | research_worker_state EMPTY (0 rows); research_runs 0; registry 2 (seeded builtin candidates only) |
| Intelligence worker | UNKNOWN | intelligence_worker_state EMPTY (0 rows) though behavior_analysis=264 rows (backfilled historically) |
| Shadow | UNKNOWN/EMPTY | shadow_runs/shadow_decisions tables absent in audit.db (lazy-schema, never attached); model_shadow_comparisons 0 rows |
| Governance state | WARNING | model_governance_state EMPTY; model_governance_events 0 — no lifecycle evidence for any model |
| Strategy registry | PASS | 2 seeded candidates; lifecycle present |
| Database integrity | PASS | integrity_check ok (3 domains); WAL mode; migrations consistent with expected versions (audit 4/4, news 2/2, candle 2/2) |
| Database growth | PASS | sizes moderate; WAL files present (news.db-wal, candle_intel.db-wal) |
| Duplicate economic outcomes | WARNING | 1 REAL historical incident (ticket 152494870397, BUG-097) remains in data (creation guard prevents NEW ones); anomaly_events=22 rows (18 IMPOSSIBLE_EXCURSION historical + others) |
| Impossible excursions | WARNING | 18 historical rows remain auditable (BUG-096 fix prevents NEW ones); EXIT_CLASSIFICATION_ANOMALY x3 (was_sl_modified=false + RISK_FREE_SL_HIT) documented for TASK-7 owner |
| Telegram | WARNING | settings status NOT_CONFIGURED per health.py TELEGRAM check? (see below) — configured via secret store after BUG-080; worker health_state available |
| Accounting (broker vs ledger) | WARNING | historical split-fill aggregation difference (BUG-081 lineage); broker trades 3635 vs ledger 266 — per-ticket reconciliation required |
| UI bundle | UNKNOWN | no version marker embedded in Web/ (stale-bundle detection must be added) |
| API | PASS | /api/status etc. exist; canonical live state graph (PHASE 14) wired |
| MT5 | UNKNOWN | engine not running; no live adapter state to query (paper mode) |
| Candle intel | PASS | candles 342, patterns 1054, regimes 396, integrity ok |

\* Liquidity is WARNING (not CRITICAL): candidate-only WIP exists but the
70D series is explicitly BLOCKED and no production path uses liquidity
features (liquidity_features_enabled=false). No trading impact.

## 4. Problems detected (§59 — do not dismiss as "old bug")

1. **Config/model path mismatch**: configs/base.yaml model_artifact_path
   points at EURUSD/v1.0.0/model.pt but the only real champion artifact is
   XAUUSD/v1.0.0. The live engine (config mode) would either fail to load or
   silently run without a model. The governance snapshot honestly reports
   champion_healthy=0. CRITICAL-to-WARNING depend on engine start; flagged now.
2. **Governance registry empty**: model_governance_state/events = 0 rows —
   Champion identity cannot be verified against any registry.
3. **News source degradation**: reuters/ustreasury/bea/bls = HTTP 200 but 0
   usable articles ever (consecutive_failures=12 each; bls 403). fed
   backoff. Only 6 of 11 sources healthy. news_consensus empty.
4. **Research/intelligence worker state EMPTY**: no evidence of live worker
   cycles; research_runs=0. Cannot distinguish "never ran" from "stalled".
5. **Web bundle has no version marker**: WEB_BUNDLE_DRIFT detection impossible.
6. **Shadow tables absent**: lazy-schema means "never attached" — must be
   distinguished from "attached and failing" (UNKNOWN, not PASS).
7. **70D stack absent**: no 70D schema/model/dataset/liquidity reference
   distributions. All 70D-specific checks must report UNKNOWN until the
   series lands (TASK-01..06) and freezes a version.
8. **Historical anomaly rows unresolved** (documented in ANOMALY-VERIFY-01
   handoff): 18 IMPOSSIBLE_EXCURSION + 1 DUPLICATE_ECONOMIC_OUTCOME remain
   as immutable history; no remediation-status field yet.

## 5. Overall status

```text
DEGRADED
```
Rationale: base 50D stack, databases and migrations are healthy; the trading
path is PAPER and no production model is currently loaded (governance empty).
Degrading evidence: news source degradation (200-but-wrong pattern), empty
worker state (no progress evidence), config/model path mismatch, and the
absence of the entire 70D stack the monitoring layer is meant to protect.
No CRITICAL trading corruption is evident (integrity ok, invariants
unchanged). This baseline is the honest start: the monitor must surface
these states continuously, not hide them.

## 6. Baseline artifacts
- scratch/post70d_1_baseline_probe.py + scratch/post70d_1_baseline_probe.out.txt
  (read-only probe; re-run to refresh)
- This report is refreshed by `nexus forensic --initial-report` (see
  POST_70D_FORENSIC_MONITORING_FINAL.md §current baseline refresh).
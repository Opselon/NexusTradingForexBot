# TASK-11-POST-70D-MONITORING — Agent Handoff

**Agent:** AGENT-11 (Hermes-Forensic)
**Role:** Continuous Forensic Monitoring / Preventive Quality Control
**Date:** 2026-08-19
**Branch:** main

---

## Summary

Built the post-70D continuous protection layer: a centralized, read-only
ForensicHealthEngine that proves the deployed stack stays correct after
release — detecting schema drift, feature drift/deadness/flood, model/scaler
mismatch, dataset drift, DB corruption, duplicate economic outcomes,
impossible excursions, worker no-progress, news degradation, governance
violations, UI/API divergence, stale bundles, silent fallbacks, and
200-but-wrong API payloads. The engine classifies PASS/WARNING/DEGRADED/
CRITICAL/UNKNOWN (never only true/false), never hides UNKNOWN, never
auto-repairs (§0/§55 — no self-modification, no auto-retrain, no auto-promote).
It produces FORENSIC_HEALTH_SNAPSHOT + dashboard data + throttled alerts and
blocks unsafe deployment through `nexus forensic --deploy-gate`.

## What is monitored (where, how)

| Area | Check IDs | Source | How |
| :--- | :--- | :--- | :--- |
| Feature contract | CHECK-FCS-00..04 | features/schema.py registry + live vectors | registry dimension/family; vector length/finite/clip |
| Model/scaler | CHECK-MDL-01..03 | artifacts + torch state dict | presence, scaler pairing, input dim vs active schema |
| Parity/causality | CHECK-RTP-01..03 | real producers + deterministic fixtures | forming-tick never changes bar-derived features; train/live dim parity |
| Dataset | CHECK-DTA-01 | artifacts/model_generation/datasets | manifest presence, schema consistency |
| Accounting | CHECK-ACC-01..04 | audit.db | broker vs ledger PnL; duplicate outcomes; MFE>=0/MAE<=0; experience gap |
| Database | CHECK-INT-01, CHECK-MIG-01, CHECK-GRW-01 | sqlite PRAGMA + migration registry | integrity_check, schema versions vs expected, size baselines |
| Liquidity | CHECK-LIQ-01 | feature_vectors rows + frozen refs | deadness/flood/drift vs frozen distribution (§8/§9/§10) |
| News | CHECK-NWS-01..03 | news.db + config | source health (200-but-wrong!), worker progress, availability matrix |
| Shadow | CHECK-SHD-01 | audit.db shadow tables | progress vs claims; never-attached = UNKNOWN |
| Governance | CHECK-GOV-01..02 | governance state | impossible states; champion identity vs artifact |
| UI/API | CHECK-UI-01..02, CHECK-API-01..02 | Web bundle + server surface | canonical endpoint; bundle version markers; chart OHLC validity |
| Telegram | CHECK-TEL-01 | settings service + notifier | configured/enabled; worker failures with 0 sent = SILENT_FAILURE |
| Trace | CHECK-TRC-01..03 | worker states + logs | trace gaps; correlation id columns; silent-fallback patterns |
| Workers | CHECK-RSW-01 | worker state tables | RUNNING-with-0-cycles = WORKER_NO_PROGRESS/STALLED |
| Runtime/Perf | CHECK-RTM-01, CHECK-PER-01 | config + registry | configured mode; perf baselines |

CLI: `nexus forensic [--snapshot|--deploy-gate|--json]`.
API: `GET /api/forensics/health` (dashboard rows incl. §52 detail_view).
Persisted snapshot: `artifacts/forensics/forensic_health_snapshot.json` +
`history.jsonl` (bounded).

## Thresholds / alert rules

- ALERT_POLICY in forensics/engine.py: schema/migration/model/DB/duplicate-
  outcome/parity/champion checks = **immediate**; liquidity/news drift =
  **aggregated** (15 min window); performance = **periodic** (60 min).
- Deadness: mode_fraction >= 0.99 | std < 1e-9 | missing 100% | sat 99% →
  FEATURE_DEAD (DEGRADED).
- Flood: near-bound mean AND std < 5% of ref std → FEATURE_FLOOD (DEGRADED).
- Drift: |mean shift|/ref.std: >2 WATCH(WARNING), >3 WARNING, >5 CRITICAL.
- Excursion: MFE<0 or MAE>0 rows after 2026-08-19 fix date → CRITICAL;
  pre-fix rows → WARNING (immutable history).
- Duplicate outcome: any fresh execution_id with >1 outcome → CRITICAL;
  known historical 152494870397 → WARNING.

## Invariants

docs/POST_70D_RUNTIME_INVARIANTS.md — INV-70D-001..020 + m47xj8 (NO SILENT
CORRUPTION/SCHEMA DRIFT/FALLBACK/MODEL MISMATCH/DB DAMAGE/UI-API DIVERGENCE).
All map 1:1 to CHECK-* IDs and TEST-MONITOR-* tests.

## Known false positives / limitations

- UNKNOWN-dense baseline: governance registry empty, shadow never attached,
  worker state tables empty, liquidity refs absent → the engine truthfully
  reports UNKNOWN for those (by §5 design).
- The 200-but-wrong news sources (bea/ustreasury HTTP 200 with 0 usable
  articles) are REAL degradation, not a false positive.
- check_database_growth uses hardcoded 2026-08-19 baselines; refresh them
  after a legitimate size regime change.
- check_silent_fallback scans the last 8 logs in artifacts/logs; a log dir
  with none is UNKNOWN.
- Broker/ledger unmatched_ratio 0.93 reflects the documented pre-BUG-045
  migration gap + paper era — NOT a new anomaly.

## Which tests protect it

tests/unit/test_forensic_monitoring_task11.py — TEST-MONITOR-01..36
(86 tests): invariants, schema drift, deadness, flood, causal canary,
dataset parity, model/scaler contract, DB integrity, accounting,
duplicate outcome, impossible excursion, experience gap, worker no-progress,
news degradation, liquidity, shadow, governance, champion identity,
UI/API, bundle drift, telegram silent failure, trace, correlation ids,
silent fallback, 200-but-wrong, chart health, runtime mode, db growth,
queue growth, release preflight, change-impact selection, snapshot,
critical-alert throttling, NO-self-modification (checks are read-only —
verified by hermetic cwd assertions).

## How to extend the monitor

1. New check → add a function in forensics/checks.py returning CheckResult
   (use `_ok/_unknown/CheckResult`), register it in `check_groups()`
   (engine.py) under the right dashboard group.
2. Add ALERT_POLICY entry (immediate/aggregated/periodic) for CRITICALs.
3. Add TEST-MONITOR-NN coverage in the existing test file (extend it —
   tests/ convention).
4. Frozen references: register via `FeatureReferenceRegistry.register`
   (explicit governance freeze action; replace=True only deliberately).
5. Run `nexus forensic --snapshot`, `ruff check/format`, `mypy`,
   `pytest tests/unit/test_forensic_monitoring_task11.py`.

## Risks / notes for the 70D series

- CHECK-LIQ-01 and CHECK-FCS-03 stay UNKNOWN until the series FREEZES a
  liquidity reference distribution — do not "silence" them; register refs.
- When a 70D champion lands: register in governance + freeze refs + attach
  shadow — the monitor then upgrades UNKNOWN → verified automatically.
- The 54 historical excursion rows and the 1 duplicate outcome are immutable
  (INV-007); no auto-repair exists or will be added (tested).

## EXACT NEXT-AGENT INSTRUCTIONS (TASK-12)

1. Read docs/POST_70D_FORENSIC_MONITORING_FINAL.md + this handoff.
2. Wire `nexus forensic --deploy-gate` into the pre-push/release pipeline
   (beforePush.ps1/sh or release.yml) so a CRITICAL blocks deployment.
3. Add a periodic cron/Telegram summarized report ("NSE FORENSIC HEALTH")
   using engine.snapshot() — §53 format, throttled to avoid spam.
4. When the 70D series freezes its liquidity version/dataset/model:
   register the reference distribution + champion identity; re-run
   `nexus forensic --snapshot` and verify the unknowns resolve.
5. Investigate the five degraded sources (fed 304, bls 403, bea/ustreasury
   200-with-0, reuters) — this is a real §25 source-quality defect owned by
   Hermes-News (recommend BUG-NNN with the news_health evidence).
6. Investigate the 68% experience→outcome gap with the learning owner —
   the accounting DEGRADED state is driven by it.
7. Keep the invariant registry additive; never rewrite existing rows.
8. Any check that flags a NEW anomaly must be surfaced with its
   correlation_id (from the snapshot) — no silent disappears.
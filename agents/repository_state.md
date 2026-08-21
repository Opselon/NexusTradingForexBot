# REPOSITORY STATE SNAPSHOT — Nexus Scalp Engine (NSE)

> Maintained per the MASTER MULTI-AGENT CONTRACT §6 (see `agents/multi-agent-git-contract.md`).
> This is the project's CURRENT MAP. Refresh after substantial work.
> Snapshot taken: 2026-08-18 (contract registry initialization).

## Git state
- Branch: `main`
- HEAD: `4d5ef5d` — feat(ui): Account Performance & Intelligence panel UX v2 — hero cards, grouped metrics, deep scenario analysis (2026-08-18 04:50 +0330)
- Working tree: DIRTY (188 entries at snapshot) — parallel sessions' uncommitted work present (UI v2, BUG-081 forensics, settings subsystem, news keywords, scratch probes). DO NOT overwrite/reset/stash (contract §1).

## Architecture
- Full inventory in `agents/skill.md` (2437 lines, 🟢 forensic badges). Layer map: Domain (frozen Pydantic) → Ports (IMT5Port) → Adapters (mt5/paper/database) → Features (50D) → Models (ScalpNet 4-class) → Training (walk-forward, triple-barrier) → Signals (policy + rule matrix) → Risk (dynamic volume) → Execution (order_manager) → Application (live_engine async) → Web/API.
- Active model: ScalpNet (PyTorch), input (Batch, 50), 4-logit head (NO_TRADE/BUY/SELL/WAIT), Champion/Challenger lifecycle (Phase 10-11).
- Feature schema: 50D master contract (float, finite, clipped [-3.0, +3.0]); 60D/350D variants schema-controlled (INV-009).

## Databases
| DB | Responsibility | Path |
| :--- | :--- | :--- |
| audit.db | trading ledger, experience outcomes, accounting, research registry, strategy lifecycle | artifacts/audit.db |
| news.db | news ingestion, analysis, consensus, impacts | artifacts/news.db |

Write path: AuditRepository queues writes to a background worker thread (no sync DB on tick path, INV-001). Shadow tables are LAZY-schema (ensure_schema on first save).

## Workers / runtime
- LiveEngine async event loop (tick pipeline, bar aggregation, state sync, retrain orchestrator).
- AuditRepository background writer thread.
- Research worker (seed builtin candidates → dataset → discovery each cycle).
- Training worker via asyncio.to_thread (auto_train_enabled=False).

## Subsystem state
- MT5 integration: broker-aware providers (Phase 14); MT5 epochs are SERVER-LOCAL (broker GMT+3, NOT UTC) — BUG-070.
- News: Phase 12 engine, keyword lexicon 189 keywords, /api/news/keywords pattern cache fixed.
- Experience: outcome recovery + broker reconstruction (Phase 14); BUG-073/081 related work in progress.
- Accounting: Phase 08 unified core + advanced metrics; retention module new.
- Research: registry + strategy lifecycle; BUG-075 fixed (null score).
- UI: Web dashboard, Account Performance & Intelligence panel v2 at HEAD.
- Release: health.py / verify.py present; RELEASE.md in docs/.

## Active bugs / blockers
- Bug ledger: `agents/bugs.md` reached BUG-081 (split-fill context leak + no-order-ID duplicates + exit-classifier falsehoods — in progress).
- Data Gate: own numbering (M5 dataset passed; BUG-060 OPEN).
- Known OPEN at snapshot: BUG-060 (Data Gate), BUG-081 follow-ups, retention/perf P3 items (see skill.md §19).

## Active agents
- Parallel sessions are actively working (evidence: 188-entry dirty tree, BUG-072..081 forensics from 2026-08-18). Check `agents/locks.yaml`, `agents/taskboard.md`, and git status before claiming any path.

## Refresh rule
Update this file additively after substantial work (new modules, model changes, DB changes, major bug fixes). Never delete historical state notes — append a new snapshot section.

## Refresh (2026-08-18, TASK-9)
- NEW `src/nexus_scalp/release/updater.py` (full update engine + orchestrator).
- `nexus update` now exposes check/status/history/rollback/doctor.
- release-manifest.json is embedded in the portable tree (CI + build script).
- EXIT_UPDATE=5 added (additive).
- REAL Windows experiment performed: v9.0.0 -> v9.1.0 over a local GitHub
  stub; user data fully preserved; app-tree data dirs preserved across swap.

## Snapshot 2026-08-18 (TASK-5 model intelligence)
- Feature schema: `scalp_v1` (50D) remains ACTIVE. `scalp_v2` (60D) is now
  PRODUCIBLE (features/schema_augment.py + model_generation/schema_v2.py) but
  candidate-only — no live switch.
- Model lifecycle: VALIDATION gates hardened (OOS macro-F1 floor 0.34,
  balanced-accuracy floor 0.34, ECE floor 0.15, min-evidence 100 rows).
  Benchmark matrix now 8 cells (50D/60D × news off/on × LEGACY/TCN).
- Training worker status truthful: DISABLED when auto_train_enabled=False.
- Champion frozen: `artifacts/models/scalp/XAUUSD/v1.0.0` (hash
  f0f70efb…) — untouched; snapshot `docs/task5_champion_baseline.json`.
- Controlled experiment (real M5, 99,946 rows): A/B/C/D all REJECTED;
  60D improved acc/ECE directionally but no candidate cleared the gates;
  news verdict NEWS_INCONCLUSIVE_NO_OVERLAP (news DB postdates dataset).
- Bug ledger: BUG-083 appended (60D had no producer). INV-015/016 added.
- Active bugs at snapshot: BUG-060 (Data Gate), BUG-081 follow-ups.

## Snapshot 2026-08-18 (TASK-2 — behavioral/anomaly intelligence landed)

- TASK-2 (Hermes-Behavior) VERIFIED: BehaviorDetectionEngine now wired into IntelligenceWorker (`_refresh_behavior`, off hot path, bounded 200 trades). New evidence-gated detectors (OVERHOLD_LOSER, PROFIT_GIVEBACK, MISSED_BREAKEVEN, PREMATURE_BREAKEVEN, MODEL_REVERSAL_IGNORED, REGIME_CHANGE_IGNORED, LIQUIDITY_REVERSAL_IGNORED, RISK_DEVIATION, EXIT_CLASSIFICATION_ANOMALY, STRATEGY_CONTEXT_LOSS, DUPLICATE_ECONOMIC_OUTCOME). Versions `behavior-v1`/`anomaly-v1`.
- New DB tables: `behavior_analysis` (versioned idempotent derived records), `anomaly_events` (deterministic-id evidence store). Backfilled artifacts/audit.db: 264 trades, 225 flags, 22 anomalies, 99.46% coverage, 0.1s, idempotent.
- Reporting: BehavioralSection + AnomalyStateSection carry truth states (NO_DATA/CLEAR/FLAGS_FOUND/ANOMALIES_FOUND), evidence coverage, engine versions. Telegram/API consume them; `n/a (no behavioral flags recorded)` and `none detected` eliminated.
- API: `/api/account/performance/intelligence` gains compact `intelligence` block; new `/api/intelligence/anomalies`.
- BUG-094 (behavior pipeline disconnected) FIXED. Handoff: `docs/agent_handoffs/2026-08-18_TASK-2-behavior-anomaly-intelligence.md`.

## Snapshot — TASK-11 (Database Hygiene, 2026-08-18)

- NEW: `src/nexus_scalp/hygiene/` (retention, detectors, archive, worker,
  state, worker_runner) — non-destructive DatabaseHygieneWorker.
- NEW: CLI `nexus db hygiene status|plan|run|pause|resume|history` (+--json);
  API `GET /api/db/hygiene`; live_engine 6h hygiene hook (AUDIT_ONLY first run).
- Docs: docs/DATABASE_HYGIENE_MATRIX.md (per-table tiers/retention/owners),
  docs/DATABASE_HYGIENE.md (policy), handoff TASK-11-database-hygiene.md.
- Registries: BUG-099, INV-017, CHG-0006 VERIFIED, contracts DATABASE_HYGIENE
  v1 + RETENTION_POLICY v1, skill.md §15l, taskboard TASK-11 VERIFIED.

## Snapshot 2026-08-19 (TASK-10 — database migration system landed)

- TASK-10 (Hermes-DBMigrate) VERIFIED: canonical `src/nexus_scalp/database/`
  migration engine — per-domain schema versions, checksummed migration
  registry, baseline detection, WAL-safe backups, OS-lock concurrency control,
  drift detection, downgrade block, startup gate, `nexus db` CLI (same
  engine), TASK-9 updater integration, health/doctor + `/api/db/status`.
- Schema versions: audit=4, news=2, candle_intel=2 (all migrated in place on
  artifacts/*.db; financial/news/research invariants verified unchanged).
- New docs: `docs/DATABASE_MIGRATIONS.md`, RELEASE.md §11, skill.md section,
  contracts DB_MIGRATION v1 + SCHEMA_MANIFEST v1, INV-013.
- Tests: tests/unit/test_database_migrations_phase18.py (TEST-DBM-01..40) +
  test_cli_db_phase18.py; large-DB probe (100k orders/50k ledger → 0.24s,
  idx_orders_ticket 4.32ms→0.02ms, integrity ok, idempotent).

## Snapshot 2026-08-19 (TASK-07-70D-LIQUIDITY-RESEARCH — BLOCKED registration)

- HEAD: 4001e4c (origin/main in sync). NEW 70D task series (TASK-01..06 as
  defined by the 70D workflow) has NOT landed. No docs/70D_*.md, no
  docs/MODEL_BENCHMARK_70D_LIQUIDITY.md, no docs/LIQUIDITY_70D_OPTIMIZATION_REPORT.md,
  no agent_handoffs for the 70D series (TASK-01..06 handoff files belong to
  the OLD Phase 13-16 series).
- Working tree (UNCOMMITTED, parallel-agent WIP — DO NOT touch/commit):
  - NEW src/nexus_scalp/features/liquidity_engine.py (TASK-01-60D-LIQUIDITY engine).
  - features/schema.py: registered scalp_liquidity_v1 (60D, feat_50..59) — candidate-only.
  - model_generation/schema_v2.py: +182 lines compute_liquidity_frame (scalp_liquidity_v1 builder).
  - configuration/config.py + configs/base.yaml: liquidity_features_enabled=false (explicit switch).
  - tests/helpers/liquidity_fixtures.py + tests/unit/test_liquidity_engine_contract.py.
  - docs/LIQUIDITY_60D_FORENSIC_BASELINE.md + docs/LIQUIDITY_60D_50D_CONTRACT_SNAPSHOT.json.
- TASK-01 contract test status: 3/13 FAIL (test_liq03_vector_is_exactly_60,
  test_liq05_all_values_clipped_minus3_plus3, test_liq11_far_apart_highs_not_a_cluster).
- No 70D datasets/models: artifacts/model_generation/datasets/ has 4 older ds_* dirs
  (Aug 18); models/ has bench_*/cand_data_gate_* (Aug 17-18). audit.db shadow tables:
  only model_shadow_comparisons (old governance), no liquidity tables.
- TASK-07-70D-LIQUIDITY-RESEARCH registered BLOCKED (BLOCKED_ON_FROZEN_LIQUIDITY_VERSION);
  no research run performed, no production code touched (contract: research over a moving
  implementation is forbidden). Next action belongs to the 70D series owner (TASK-01..06
  must land + freeze an algorithm version, then this task can bootstrap).

## Snapshot 2026-08-19 (TASK-13 git surveillance)

- HEAD: c56d3340814a763b9b1aa79b370ec63f9ad73ae8 (origin/main in sync) at surveillance
  start. Working tree: 55 entries (23 modified + 32 untracked) — 70D swarm mid-flight:
  TASK-01 liquidity foundation (liquidity_engine.py + scalp_liquidity_v1 + schema_v2
  builder + config switch + 3 test files), TASK-02 integration (LiquidityGovernor +
  scalp_v4 70D schema + Web UI + /api/liquidity/*), TASK-04 model validation
  (test_70d_model_validation_task4.py, BLOCKED on TASK-03 parity), TASK-05 shadow70
  (runtime/health/drift/store/worker + test_shadow70_runtime.py + Web API), TASK-08
  promotion governance (AUDIT-0005 migration 4->5, emergency controls, transaction/
  lock/verify, 17 governance endpoints), TASK-11 forensics monitor (forensics/ package
  + POST_70D docs), TASK-12 incidents (incidents/ package + cli incident_commands).
- Liquidity test state CORRECTED: 5 FAILING (liq11 far-apart-highs, liq16 confluence,
  liq21 sweep-then-reclaim, liq25 future-htf, liq45 dataset-shape) — the earlier
  snapshot's claim (3 failing: liq03/05/11) is STALE; owner TASK-01.
- Registry state synced additively by TASK-13: CHG-0014, TASK-13 row, BUG-102, INV-018
  (already landed by TASK-05), BUG-100 (TASK-05), BUG-101 (TASK-05/08 RNG seeding —
  parallel agent, NOT this task).
- Known failures: beforePush gate stays RED until TASK-01 fixes its 5 liquidity tests;
  no CI workflow runs on push-main (release.yml targets tags).
- Next milestone: TASK-01 coherent commit (fix 5 tests -> commit foundation alone),
  then TASK-02/03/04 chain; TASK-07 research still BLOCKED_ON_FROZEN_LIQUIDITY_VERSION.

## Snapshot 2026-08-19 (TASK-02-70D-INTEGRATION)
- 70D integration contract `scalp_v4` registered (BASE 0..49 | FAMILY 50..59
  | LIQUIDITY 60..69); `scalp_v3`/350D untouched.
- New `features/liquidity_runtime.py` (LiquidityGovernor + build_70d_vector +
  resolve_model_compatibility). Live engine computes the 10 liquidity values
  on new-bar cadence (information-only).
- New API: GET /api/liquidity/state, GET /api/liquidity/features,
  POST /api/liquidity/toggle; canonical /api/status + /api/live/state embed a
  `liquidity` section (SSE carries it).
- UI: Liquidity Intelligence tab (status/schema/values/toggle) + chart
  pool overlays from real snapshot pools.
- Tests: tests/unit/test_liquidity_runtime_integration_phase18.py (30),
  tests/integration/test_liquidity_api.py (9); 99 liquidity tests total pass.

## Snapshot 2026-08-19 (TASK-9 — 70D production release layer, Hermes-ProdRel)

- NEW `src/nexus_scalp/release/model_artifacts.py` — artifact identity +
  ACTIVE/LEGACY/RETAINED/ARCHIVABLE classification + 70D dependency check
  (MODEL_NOT_RUNTIME_COMPATIBLE, no silent fallback). CLI:
  `nexus model-artifacts [--json]`.
- NEW `src/nexus_scalp/release/versioning.py` — RuntimeVersionBlock:
  app/commit/web_bundle(hashed)/feature_schema/db_schema with
  VERSION_INCONSISTENCY drift verdict. Wired into `/api/status`
  (`versioning` block) + `nexus version --json`.
- RELEASE MANIFEST schema coverage: feature_schema/supported_model_
  schemas/web_bundle_version/db_schema_version/required_migrations all
  registry-derived (BUG-109 fixed: was hardcoded scalp_v1/50D).
- MIGRATION: AUDIT-0007-release-metadata (audit v6 -> v7) — additive
  release_metadata key/value table; tested on a copy of the real
  artifacts/audit.db (ledger/PnL unchanged, integrity ok). INV-018
  documents the manifest/baseline interaction (BUG-108 class fix).
- TESTS: TEST-REL-01..30 acceptance now executable —
  test_release_{model_artifacts,versioning,manifest,migration_0007,
  acceptance}_phase19.py (42 new tests) + phase17 update suite (42) +
  phase18 migration suite (43) all green. Full release suite: 84.
- DOCS: docs/70D_PRODUCTION_RELEASE_FORENSICS.md,
  docs/70D_INSTALLATION_COMPATIBILITY.md, docs/70D_PRODUCTION_DEPLOYMENT.md,
  docs/70D_UPDATE_AND_MIGRATION.md.
- REGISTRIES: contracts MODEL_RELEASE v1 + VERSION_CONSISTENCY v1,
  INV-018/019, CHG-0013 VERIFIED, BUG-109 appended, taskboard TASK-9
  VERIFIED. Schema versions: audit=7, news=2, candle_intel=2.
- Parallel-session 70D liquidity working tree (liquidity_engine.py,
  scalp_v4 registration, liquidity_features_enabled flag) remains
  UNTOUCHED and read-only for this task.

## Snapshot 2026-08-19 (TASK-03-70D-PARITY - 70D contract landed)
- scalp_v3 redefined 350D -> 70D canonical (Base|News|Liquidity). Registered: scalp_v1
50D (ACTIVE), scalp_v2 60D (candidate), scalp_v3 70D (candidate, canonical), scalp_v4 70D
(TASK-2 integration candidate), scalp_liquidity_v1 60D (TASK-1 candidate).
- NEW: features/schema_contract.py, features/features70.py, features/inference_validator.py,
features/runtime70.py, model_generation/replay.py replay_70d_vector, schema_v2
compute_70d_frame/build_70d_dataset/verify_70d_artifact, manifest feature_schema_hash +
training_dataset_id.
- Tests: tests/unit/test_70d_{contract,dataset,replay,validator,runtime,perf}_task3.py
(70+ cases), tests/helpers/golden70d.py (11 scenarios).
- Docs: docs/70D_DATA_CONTRACT.md, docs/agent_handoffs/TASK-03-70D-PARITY.md.
- Commits: 3cc53a3, 09dd0bc, 5401d7f, 14fff5a, b531243, abafa9c (all pushed).
- Next: TASK-4 benchmark UNBLOCKED; TASK-5 shadow70 can consume a validated candidate.

## Snapshot 2026-08-19 (TASK-08-70D-GOVERNANCE — governance control plane)

- TASK-8 (Hermes-GovAgent8) extended the TASK-6 governance boundary for the
  70D challenger lifecycle WITHOUT promoting anything (evidence chain still
  INSUFFICIENT_EVIDENCE until TASK-03 parity + a real validated candidate).
- New: governance/verify.py (14-gate verify_candidate, SKIP=INSUFFICIENT),
  governance/transaction.py (atomic promotion txn, crash-recoverable audit
  states), governance/lock.py (cross-process promotion lock); engine
  promotion_preview/rollback_preview/emergency freeze+disable; store audit
  persistence; load_gate reads the canonical schema registry; migration
  AUDIT-0005 (model_promotion_audit + model_rollback_audit).
- API: /api/models/governance/{status,promotion-preview,rollback-preview,
  audits}, /api/models/promotion/execute, /api/models/governance/emergency/*
- UI: Promotion Controls block (preview/promote/freeze + status badge).
- Tests: TEST-GOV-01..30 (TestGovernance70), TestGovernance70API (7).
- BUG-108: AUDIT-0007 release_metadata migration baseline-skeleton fix.

## Snapshot 2026-08-19 (TASK-12 — incident response layer)

- NEW `src/nexus_scalp/incidents/` (models/store/correlator/lineage/trace/
  impact/reports/worker/telegram) — canonical incident model, correlation
  engine, value lineage, WHY workflows, impact analysis, quarantine,
  approval-gated recovery plans, secret-masked reports.
- AUDIT-0006 additive migration (incidents/events/traces/quarantine tables)
  applied to artifacts/audit.db (schema v7, integrity ok).
- CLI `nexus incidents list|show|search|stats|report|export|scan|trace-why|lineage`.
- API GET /api/diagnostics/{incidents,incidents/{id},health,lineage,search}
  (read-only). Web Forensic Incident Center tab.
- 5 real baseline incidents persisted (1 CRITICAL ACCOUNTING_DIVERGENCE,
  1 HIGH TIMEBASE_DIVERGENCE, 3 MEDIUM) from the read-only forensic scan;
  reports in artifacts/incidents/.
- Absorbed into commit 066a7ba (parallel agent); attribution in taskboard.

## Snapshot 2026-08-19 (TASK-09 70D candidate validation)

- HEAD e7586f9 (origin/main in sync at verification): BUG-106 FIXED (bounded 4000-bar
  liquidity history, O(n^2)->O(n x 4000); docs/BUG-106-PERFORMANCE-FIX.md; parity green).
- 70D parity: GREEN (68 passed/3 skipped; real-data probe 0 mismatches exact).
- Real 70D dataset: ds_d3f35b12d63148da (XAUUSD M5, 1146 rows, scalp_v3, hash aad73c8f..).
- Champion: 9105cef7 (RESTORED_CANDIDATE per BUG-104; original f0f70efb unrecoverable;
  operator decision pending per INV-015). Champion untouched by TASK-09.
- Candidate chain: NO_CANDIDATE -> training/walk-forward/OOS/robustness/calibration/shadow
  all NOT_RUN -> INSUFFICIENT_EVIDENCE -> NOT_ELIGIBLE. No promotion (INV-015).
- BUG-108 fresh-DB regression green (38 passed). BUG-110 recorded (manifest temporal_range
  1970-01-01 writer artifact; dataset content verified correct).
- Parallel WIP in tree (model_lifecycle AI-Hub diagnostics, liquidity_runtime, schema_v2,
  tests) untouched. See docs/TASK-09-70D-CANDIDATE-VALIDATION-FINAL.md.

## Snapshot 2026-08-19 (TASK-13 — incident runtime activation)

- IncidentWorker wired into live_engine (60s throttle, asyncio.to_thread, off
  tick path, lazy construction, shutdown hook) with structured telemetry
  (IncidentTelemetryCollector + emit_incident_telemetry from MT5/execution/
  reconciliation failures).
- Worker state machine STARTING/RUNNING/DEGRADED/STOPPING/STOPPED/FAILED +
  latency p50/p95/p99 + useful-work telemetry (spec 7/39).
- AccountingForensicsEngine: BUG-114 PROVEN — 151 zero-PnL ledger rows from
  NONE-fallback reconstruction persisted as final; first divergence LEDGER;
  RECONSTRUCTION_FAILURE x151; 32 zero outcomes RECOVERABLE_FROM_BROKER;
  151 recovery candidates (RECOMMENDED, never auto-applied).
- TimebaseProbe: HISTORY_QUERY_ERROR — DB clock healthy (-0.7s), broker
  offset = bulk sync-lag; TIMEBASE_DIVERGENCE incident resolved
  HIGH_CONFIDENCE.
- Telegram CRITICAL/HIGH alerts wired + verified end-to-end (1 alert, dedup).
- artifacts/forensics/accounting_divergence.json + timebase_probe.json.

## Snapshot 2026-08-19 (TASK-CI-ISOLATION — Hermes-CI-Isolation)

- GitHub Actions audit COMPLETE: 4 workflows (ci/release/docker/security).
- ci.yml: 'quality' = ruff/format/mypy/unit+coverage on every push+PR to
  main|develop; NEW 'heavy-ci' 4-arm matrix (integration, e2e Playwright,
  research-backtest, model-validation) runs ONLY on ci-tests branch or
  manual dispatch inputs.full=true. Concurrency (cancel-in-progress) kept.
- security.yml: push-triggered CodeQL/Trivy on every main/develop push
  REMOVED; now PR to main|develop + push to ci-tests + weekly Monday 06:00
  UTC schedule + workflow_dispatch. Concurrency group added.
- docker.yml / release.yml: triggers UNCHANGED (docker branch / v* tags +
  dispatch). Comment-only doc blocks added to docker.yml.
- ci-tests branch created from origin/main for heavy CI execution.
- Validation: actionlint clean on all changed files (release.yml carries a
  pre-existing ::set-output deprecation warning at line 247, untouched).
- Normal pushes to main/develop now run ONLY the fast quality gate;
  heavy suites run on ci-tests or manual dispatch.

## Snapshot 2026-08-19 (TASK-CI-REPORTING — Hermes-CI-Reporting)

- Unified CI results system live in ci.yml: every check writes to its own
  ci-results/<category>/ dir; run-info/ holds metadata.json, per-check
  status JSONs, summary.md, manifest.json, SHA256SUMS.txt, secrets-present.json.
- Quality job: ruff (json+txt) / format (txt) / mypy (txt+junit) / pytest
  (junit.xml + log + cobertura coverage.xml + htmlcov). $GITHUB_STEP_SUMMARY
  populated. One canonical artifact ci-results-${{workflow}}-${{run_number}}-${{sha}}
  (30-day retention). Final gate fails the job on any check failure while
  artifacts are still uploaded (continue-on-error only for capture steps).
- heavy-ci matrix (4 arms) each produce their own artifact; aggregate job
  merges them into ONE canonical artifact. Isolation retained: heavy CI
  still runs only on ci-tests or dispatch inputs.full=true.
- release.yml: deprecated ::set-output (arm64-report) replaced with
  $GITHUB_STEP_SUMMARY. All workflows actionlint-clean (0 errors).
- docs/ci-secrets.md documents secret NAMES/purpose/location (values never).
- scripts/ci/make_ci_results.py: authoritative CI results pipeline,
  adopted from parallel agent, verified end-to-end locally.
- 2026-08-20 stash reconciliation (TASK-STASH-MERGE-01): all 4 stashes resolved
  (2f80285, e813340, eea33a7, + 6ab3c9b, b29280d); stash salvages archived to
  archive/stash-salvage-20260820/ (git-ignored); DB-portability settings chain now
  canonical (service -> PG_CONFIG_SETTING_KEY -> config.py); 0 stashes remain.

## Snapshot 2026-08-22 (Hermes Kanban Swarm adoption — Lead Architect init)
- Hermes Kanban Swarm adopted as an orchestration pattern (integration contract:
  `agents/hermes-kanban-swarm.md`). The swarm is an orchestration layer only; the
  repository's existing multi-agent Git/forensic/runtime/ownership contracts remain
  authoritative over task state, ownership, Git history, runtime safety, forensic
  integrity, and verification.
- Decision record: `agents/decisions/DEC-0003-hermes-kanban-swarm-integration.md`
  (renumbered from the upstream `DEC-0002` to avoid collision with
  `DEC-0002-nodejs-runtime-role.md` already on main).
- Lead Architect role established for swarm coordination: owns architecture/contracts/
  ownership mapping and verification-before-merge gates; does NOT directly modify
  critical shared files (live_engine.py, order_manager.py, risk, MT5 adapters, feature
  schema, model governance, migrations, secrets) without explicit ownership.
- Swarm work maps to existing TASK-ID / BUG-NNN / CHANGE-ID registries; no second
  independent coordination state is introduced.

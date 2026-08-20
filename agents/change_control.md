# CHANGE CONTROL REGISTRY — Nexus Scalp Engine (NSE)

> Maintained per the MASTER MULTI-AGENT CONTRACT §4 (see `agents/multi-agent-git-contract.md`).
> Register a change entry BEFORE a meaningful architectural or shared-code change.
> Status lifecycle: PROPOSED → IMPLEMENTING → VERIFIED → READY_FOR_REVIEW → MERGED | REJECTED.

## Open / recent changes


```text
CHANGE-ID: CHG-0018
Agent: Hermes-ShadowLive70D
Role: 70D Shadow Deployment & Candidate Validation Engineer
Task: TASK-70D-SHADOW-LIVE-VALIDATION
Scope: Current-state verification (HEAD 3f3f3d9), 70D candidate discovery
       (wf_candidate DISCOVERED; NOT VALIDATED — unregistered, scalp_v4 vs
       canonical scalp_v3, missing provenance/validation), gated attach
       rejection proof (by design), Champion-unchanged + broker=0 proof,
       governance verify matrix via verify_candidate (read-only), evidence
       JSON + final docs + TEST-SHADOW-LIVE-01..20
Affected files: tests/unit/test_shadow70_live_valid.py (new),
       scratch/shadow70_live_evidence.py (new), docs/70D_SHADOW_LIVE_
       VALIDATION_FINAL.md (new), docs/agent_handoffs/TASK-70D-SHADOW-LIVE-
       VALIDATION.md (new), artifacts/validation/70d_shadow_live_evidence.json
       (new), agents/* registries
Affected functions/classes: none in src/ (read-only evidence task; reuse of
       Shadow70Runtime.attach + governance.verify.verify_candidate)
Contracts touched: SHADOW_70D v1 (reused), MODEL_GOVERNANCE v2 (reused),
       INV-018 (honored)
Runtime paths touched: NONE (no production mutation; shadow attach is
       exercised only against the runtime object in-process with a
       deliberately non-attached candidate)
Owners affected: Hermes-GovAgent8 (TASK-08 governance), TASK-04 validation
Risk: NONE (read-only; no promotion, no broker, no Champion touch)
Dependencies: TASK-05 shadow70 runtime, TASK-08 governance verify,
       TASK-04 wf_candidate artifact
Required tests: TEST-SHADOW-LIVE-01..20
Status: IMPLEMENTING
```

```text
CHANGE-ID: CHG-0017
Agent: Hermes-Forensic-12
Role: Incident Correlation / Root-Cause / Recovery Diagnostics Engineer
Task: TASK-12-FORENSIC-INCIDENT-RESPONSE
Scope: Incident response layer above TASK-11 monitoring: canonical incident
       model (INC-YYYY-hex8, 8 statuses, 5 severities), correlation engine
       (fingerprint + correlation_id + ticket grouping, causal chain from
       actual timestamps), value lineage (source-of-truth traces), WHY
       workflows (blocked/closed/no-learning/no-strategy/UI-empty),
       MT5/ledger divergence + clock-skew + split-fill + outcome forensics,
       impact analysis (observed-only), non-destructive quarantine,
       recovery-plan generation (RECOMMENDED, approval-gated, no
       destructive options), incident reports (JSON+MD, secret-masked),
       AuditRepository-queued persistence via governed AUDIT-0006 migration,
       CLI `nexus incidents *`, read-only /api/diagnostics/*, Forensic
       Incident Center web tab, Telegram CRITICAL/HIGH alerts with
       throttling/dedup, worker (background, bounded, off tick path)
Affected files: src/nexus_scalp/incidents/{models,store,correlator,lineage,
       trace,impact,reports,worker,telegram,__init__}.py (new),
       src/nexus_scalp/database/registry.py (AUDIT-0006), cli/main.py +
       cli/incident_commands.py (new), web/server.py (diagnostics routes),
       Web/index.html + Web/app.js (Incident Center), tests/unit/
       test_incident_response_task12.py (62 tests), tests/integration/
       test_diagnostics_api.py, docs/70D_INCIDENT_RESPONSE_MODEL.md,
       docs/70D_INITIAL_INCIDENT_FORENSIC_REPORT.md,
       docs/70D_INCIDENT_RESPONSE_FINAL_REPORT.md, agents/* registries
Affected functions/classes: Incident, IncidentStore, IncidentCorrelator,
       LineageEngine, ImpactAnalyzer, QuarantineManager, RecoveryPlanner,
       IncidentWorker, IncidentTelegramNotifier, news_incidents,
       version_consistency, broker_ledger_divergence, clock_skew,
       split_fill_groups, outcome_forensics, learning_pipeline_rates,
       why_blocked/why_closed/why_no_learning/why_no_strategy/why_ui_empty
Contracts touched: INCIDENT_RESPONSE v1 (new), INCIDENT_CORRELATION v1
       (new), VALUE_LINEAGE v1 (new), RECOVERY_GOVERNANCE v1 (new);
       consumed: AUDIT-0006 migration (new), DB_MIGRATION v1
Runtime paths touched: web server (5 new GET routes), CLI (nexus incidents),
       live_engine NOT touched (worker available for TASK-13 wiring),
       audit.db (additive tables only)
Owners affected: TASK-11 monitoring (forensics/), TASK-13 handoff
       (worker wiring + incident console), governance (no change)
Risk: LOW — diagnostic-only; no execution/risk/policy imports; additive
       migration; all routes GET; recovery never auto-executed
Dependencies: TASK-11 forensics/ (models), AuditRepository queued writer,
       TASK-10 migration engine
Required tests: TEST-INCIDENT-01..35 + integration diagnostics API tests
Status: VERIFIED (absorbed into commit 066a7ba; quality gates clean)
```

CHANGE-ID: CHG-0015
Agent: Hermes-Parity
Role: 70D Dataset/Replay/Inference/Runtime Contract Engineer
Task: TASK-03-70D-PARITY
Scope: canonical 70D feature contract (scalp_v3 = Base 0..49 + News 10D 50..59 + Liquidity 10D 60..69); deterministic feature_schema_hash; immutable 70D snapshot with provenance; dataset builder (compute_70d_frame) + quality gates + reproducibility; replay parity + anti-leakage; inference validator with explicit rejection codes; model manifest extension (feature_schema_hash, training_dataset_id) + scaler compatibility (no pad/truncate); legacy 60D protection (mismatch blocked); four News/Liquidity toggle combinations; golden corpus; real-data parity; no fake fallback values; no DB on tick hot path (INV-001). Trading behavior untouched; no Champion change; no auto-promotion.
Affected files: features/schema_contract.py (new), features/schema.py, model_generation/schema_v2.py, model_generation/models.py, model_generation/news_bridge.py, model_generation/replay.py, inference validator (new module), application/live_engine.py (guarded additive hook), tests/unit/test_70d_contract_parity_task3.py, docs/70D_DATA_CONTRACT.md, docs/agent_handoffs/TASK-03-70D-PARITY.md, agents/{taskboard,change_control,contracts,runtime_invariants,repository_state,bugs}.md (additive rows)
Contracts: FEATURE_SCHEMA_70D v1 (scalp_v3 70D canonical), FEATURE_SCHEMA_HASH v1 (new), INFERENCE_CONTRACT v1 (new)
Risk: LOW-MEDIUM (feature-contract hardening only; live 50D hot path untouched; guarded 70D hook behind config flag)
Status: IN_PROGRESS


```text
CHANGE-ID: CHG-0014
Agent: Hermes-ModelValidation-04
Role: 70D Model Generation / Fair Benchmark / Challenger Validation
Task: TASK-04-70D-MODEL-VALIDATION
Scope: Fair scientific A/B/C benchmark protocol (50D Base / 60D Base+News /
       70D Base+News+Liquidity) with identical datasets-labels-splits-purge-
       embargo-budgets-seeds; dataset fairness + liquidity feature audit +
       walk-forward/OOS/robustness/calibration gates; BUG-101 reproducibility
       fix in CandidateTrainer (seed BEFORE model construction); TEST-70D-
       MODEL-01..25 contract suite. Benchmark EXECUTION remains BLOCKED until
       TASK-03-70D-PARITY lands (feature contract not trustworthy). No model
       was trained, no threshold changed, no auto-promotion, Champion untouched.
Affected files: src/nexus_scalp/model_generation/training.py (minimal seed-order
       fix), tests/unit/test_70d_model_validation_task4.py (new), docs/
       MODEL_BENCHMARK_70D_LIQUIDITY.md (new), docs/agent_handoffs/TASK-04-70D-
       MODEL-VALIDATION.md (new), agents/* registries
Affected functions/classes: CandidateTrainer.train_candidate (seed order only)
Contracts touched: MODEL_MANIFEST v1 (unchanged), FEATURE_SCHEMA v1
       (scalp_v4 70D registered by TASK-02 — consumed, not created)
Runtime paths touched: none (model-generation only; invariant INV-001..015 intact)
Owners affected: Hermes-70D series agents 1..3 (protocol consumer),
       Hermes-ModelGovernance (validation gates)
Risk: LOW (isolated to candidate training reproducibility; no live path)
Dependencies: TASK-03-70D-PARITY (blocking), TASK-01/02 (liquidity engine WIP)
Required tests: TEST-70D-MODEL-01..25 (18 passed / 8 skip-till-task3)
Status: VERIFIED (deliverables) — benchmark execution BLOCKED by TASK-03
```

```text
```text
CHANGE-ID: CHG-0014
Agent: Hermes-GitSurveillance
Role: Multi-Agent Change Surveillance / Commit / Push / Handoff Engineer
Task: TASK-13-GIT-SURVEILLANCE
Scope: Continuous multi-agent change surveillance + commit/push governance: full
       forensic snapshot of the 70D swarm working tree (55 changed files across
       TASK-01/02/04/05/08/11/12), ownership/classification of every path,
       secret scan (PASS), shared-API alerts (schema registry, load_gate, live_engine
       hooks, AUDIT-0005), duplicate-source-of-truth scan (none), pre-existing
       failure classification (5 liquidity test failures, owner TASK-01), registry
       state synchronization, surveillance FINAL report + handoff.
Affected files: agents/bugs.md, agents/change_control.md, agents/contracts.md,
       agents/runtime_invariants.md, agents/taskboard.md, agents/repository_state.md,
       docs/TASK_13_GIT_SURVEILLANCE_FINAL.md (new),
       docs/agent_handoffs/TASK-13-git-surveillance.md (new)
Affected functions/classes: none (no production code in this change)
Contracts touched: none (registry rows only; contract state surveyed and reported)
Runtime paths touched: none
Owners affected: all 70D swarm owners (TASK-01..12) — notified via handoff
Risk: LOW (additive registry rows + docs; no code/DB/migration)
Dependencies: none (surveillance is read-only over the swarm WIP)
Required tests: TEST-GIT-01..25 (verification matrix in the FINAL report)
Status: VERIFIED (committed + pushed)
```


CHANGE-ID: CHG-0013
Agent: Hermes-Shadow70D
Role: 70D Shadow Runtime / Drift / Champion-Safe Deployment Engineer
Task: TASK-05-70D-SHADOW
Scope: 70D Liquidity Shadow runtime (TASK-5/10 brief): validated-candidate gate
       (registry: NO_VALIDATED_CANDIDATE — 70D lineage mid-flight in parallel
       TASK-01..04, so Shadow infra is implemented + verified against a
       deterministic VALIDATED-status fixture, production runtime untouched),
       shadow model contract + load validation (manifest/artifact-hash/schema/
       dimension/scaler), strict isolation boundary (no policy/risk/order
       dependency), 70D live feature build (50D canonical + 10 news + 10
       liquidity when producer present; schema-controlled), feature validation
       (finite/range/schema/freshness/provenance), deterministic idempotent
       observations, disagreement taxonomy (8 classes), feature health + drift
       (NORMAL/WATCH/WARNING/CRITICAL), bounded queue + backpressure + async
       persistence (no sync DB on tick path), Claude-style structured
       [SHADOW70] events, web API (summary/recent/health/drift/disagreements),
       replay parity + live read-only smoke + Champion/broker zero-impact proof
Affected files: src/nexus_scalp/shadow/shadow70/{models,runtime,health,drift,
       store,worker}.py (new), web/server.py, application/live_engine.py
       (observability hook guarded by shadow_enabled flag), Web/ panel optional,
       tests/unit/test_shadow70_runtime.py, tests/unit/test_shadow70_safety.py,
       tests/unit/test_shadow70_health_drift.py, docs/70D_SHADOW_RUNTIME.md,
       docs/agent_handoffs/TASK-05-70D-SHADOW.md, agents/* registries
Affected functions/classes: Shadow70Runtime, Shadow70LoadValidator,
       Shadow70Observation, Shadow70Classification, Shadow70FeatureHealth,
       Shadow70DriftMonitor, Shadow70Store, Shadow70Worker (new)
Contracts touched: SHADOW_70D v1 (new), SHADOW_LOAD_GATE v1 (new),
       SHADOW_FEATURE_HEALTH v1 (new), SHADOW_DRIFT v1 (new)
Runtime paths touched: live_engine tick path — observability ONLY, wrapped in
       shadow70_enabled flag (default false), bounded, failure-isolated
       (INV-001/002/003/004/009 intact)
Owners affected: Hermes-ModelGovernance (TASK-6 shadow reuse),
       Hermes-LiquidityFoundation (70D series), Hermes-UI (shadow panel)
Risk: LOW (observability only; no execution/risk/policy path)
Dependencies: TASK-6 governance (3cca598), model_lifecycle integrity,
       AuditRepository queued writer; 70D producer series (parallel WIP)
Required tests: TEST-SHADOW-01..35
Status: IMPLEMENTING
```


```text
CHANGE-ID: CHG-0012
Agent: Hermes-Research
Role: Forensic Verification of Duplicate Outcome + Excursion Anomalies
Task: ANOMALY-VERIFY-01
Scope: MFE tracker seeding fix, economic-duplicate outcome guard, deterministic
       anomaly ids, incident-grouped anomaly listing (DB/API/UI)
Affected files: src/nexus_scalp/execution/order_manager.py, experience/ledger.py,
       experience/intelligence.py, intelligence/behavior.py, intelligence/store.py,
       Web/app.js, tests/unit/test_anomaly_verify01_{duplicates,mfe}.py
Affected functions/classes: _ensure_ticket_bootstrap, _update_mfe_mae,
       ExperienceLedger.owner_of_execution, record_trade_outcome,
       _trade_data_anomalies, list_anomaly_events (grouped)
Contracts touched: ANOMALY v1 (deterministic incident identity + grouping),
       TRADE_OUTCOME v3 (one economic trade == one outcome)
Runtime paths touched: order_manager MFE/MAE trackers (in-memory only, no I/O,
       no broker interaction, not on the async hot path I/O)
Owners affected: Hermes-Accounting (excursion metrics), Hermes-Learning (outcomes),
       Hermes-UI (anomaly panel)
Risk: MEDIUM
Dependencies: BUG-081/095 (split-fill context), BUG-096/097/098 (this task)
Required tests: TEST-ANOM-01..28
Status: MERGED
```


```text
CHANGE-ID: CHG-0013
Agent: Hermes-ProdRel
Role: Production Deployment / Migration / Runtime Reliability Engineer
Task: TASK-09-70D-PRODUCTION-RELEASE
Scope: 70D production release layer — model artifact release packaging +
       70D/60D compatibility classification (ACTIVE/LEGACY/RETAINED/
       ARCHIVABLE) + liquidity-producer dependency check
       (MODEL_NOT_RUNTIME_COMPATIBLE, no silent fallback);
       runtime version-consistency block (app/commit/web_bundle_hash/
       feature_schema/db_schema; VERSION_INCONSISTENCY on drift);
       release manifest schema coverage (registry-derived feature_schema,
       supported_model_schemas, web_bundle_version, db_schema_version,
       required_migrations); AUDIT-0007 release_metadata migration;
       TEST-REL-01..30 acceptance suite; 3 install/deploy/update docs
Affected files: release/model_artifacts.py (new), release/versioning.py
       (new), release/packaging.py, database/registry.py,
       database/manifest.py, web/server.py, cli/main.py,
       tests/unit/test_release_{model_artifacts,versioning,manifest,
       migration_0007,acceptance}_phase19.py, docs/70D_{PRODUCTION_RELEASE_
       FORENSICS,INSTALLATION_COMPATIBILITY,PRODUCTION_DEPLOYMENT,UPDATE_AND_
       MIGRATION}.md, docs/agent_handoffs/TASK-09-70D-PRODUCTION-RELEASE.md
Affected functions/classes: generate_manifest (+_manifest_* helpers),
       RuntimeVersionBlock, compute_artifact_identity/classify_artifact/
       check_runtime_compatibility/summarize_artifacts,
       _audit_0007_release_metadata, _runtime_version_block, version_cmd,
       model_artifacts_cmd
Contracts touched: MODEL_RELEASE v1 (new), VERSION_CONSISTENCY v1 (new),
       UPDATE_SYSTEM v1 (manifest schema coverage), DB_MIGRATION v1
       (AUDIT-0007 additive release_metadata)
Runtime paths touched: /api/status versioning block (read-only addition);
       nexus version --json (additive); startup migration chain includes
       AUDIT-0007 (v6->v7); CLI model-artifacts (read-only)
Owners affected: all release consumers, web UI, DB owners (audit domain)
Risk: MEDIUM
Dependencies: TASK-9 update engine, TASK-10 migration engine, TASK-6 load
       gate, canonical schema registry (scalp_v1/v2/v4/liquidity)
Required tests: TEST-REL-01..30 (phase19 suites), TEST-UP-01..35, TEST-DBM
Status: MERGED
```


```text
CHANGE-ID: CHG-0011
Agent: Hermes-Research
Role: Strategy Research / Discovery / Validation Forensic Engineer
Task: TASK-4
Scope: Research data-integrity repair: explicit eligibility audit + rejection taxonomy,
       zero-substitution rejection (UNKNOWN != 0), family-select validation, tiered
       discovery (SMALL_SAMPLE), hard evidence-floor scoring, registry immutability,
       dataset rebuild guard, /api/research/health diagnostics
Affected files: src/nexus_scalp/research/{dataset,discovery,pipeline,scoring,worker,
       registry,store}.py, src/nexus_scalp/web/server.py, tests/unit/test_research_task4_*.py,
       tests/unit/task4_research_helpers.py, tests/integration/test_research_api.py
Affected functions/classes: ResearchDatasetBuilder.evaluate_sample/audit,
       discover_candidates (tiered), ResearchPipeline._select_family, compute_strategy_score
       (evidence floor), StrategyRegistry.upsert (immutability), ResearchWorker._refresh_dataset
       (rebuild guard), research_health_summary
Contracts touched: RESEARCH_RESULT v1 (family-select evidence), TRADE_OUTCOME v3 (consumed),
       FEATURE_VECTOR_50D (schema provenance preserved)
Runtime paths touched: research worker background cycle only (off tick hot path; INV-001 intact)
Owners affected: Hermes-Research, Hermes-Accounting (research_runs consumers)
Risk: MEDIUM
Dependencies: TASK-1/2/3 outcome repair (BUG-045/073/081) — 32 zero-R outcomes remain upstream
Required tests: TEST-RS-01..26 (test_research_task4_dataset.py, test_research_task4_validation.py)
Status: MERGED
```

## Open / recent changes (table)

| CHANGE-ID | Agent | Task | Scope | Contracts | Owners | Risk | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| CHG-0002 | Hermes-TradeLifecycle | TASK-3 | Canonical trade lifecycle/exit-intelligence repair: reason-code classifier fix (BUG-088), broker-outcome dedup, lifecycle finalize wiring + reversal capture (BUG-089), exit R fix | EXIT_CLASSIFICATION v3, TRADE_EXECUTION_CONTEXT v2 (unchanged), TRADE_OUTCOME v3 (extended) | Hermes-Accounting, Hermes-Behavior | MEDIUM | VERIFIED |
| CHG-0003 | Hermes-ModelGovernance | TASK-6 | New `src/nexus_scalp/governance/` (load gate, truthful registry, event ledger, outcome linkage, drift/calibration, rollback, health); shadow runtime 60D-aware input alignment + news-context parity + latency telemetry; API `/api/models/governance/*`, `/api/models/registry/reconcile`, `/api/models/shadow/outcomes`, `/api/models/promotion/approve`; Telegram canonical governance report; Model Governance dashboard panel; golden fixtures; live-champion registry truthfulness fix. Does NOT touch OrderManager/RiskEngine/execution paths. | MODEL_GOVERNANCE v1, MODEL_LOAD_GATE v1, SHADOW_PARITY v1, PROMOTION_STATE_MACHINE v1 (new); MODEL_MANIFEST v1, FEATURE_SCHEMA v1 (consumed) | LiveEngine, shadow/, model_lifecycle/, web/, reporting/ | MEDIUM | PROPOSED |
| CHG-0004 | Hermes-ModelIntelligence | TASK-5 | Additive 60D (scalp_v2) schema path: schema_augment.py + schema_v2.py; training/validation gate hardening; fair 8-cell benchmark matrix; truthful worker status | features/schema.py, features/schema_augment.py, model_generation/schema_v2.py, model_generation/training.py, model_generation/validation.py, model_generation/benchmark.py, model_generation/__init__.py, model_lifecycle/worker.py | FEATURE_SCHEMA v1 (scalp_v2 60D, candidate-only), MODEL_MANIFEST v1 | 50D hot-path untouched (INV-009); Champion path untouched | LOW | TASK-1..4 datasets (M5 real) | test_model_generation_phase13.py TestTask5* | IMPLEMENTING |
| CHG-0002 | Hermes-DBMigrate | TASK-10 | Automatic DB migration engine: versioned per-domain migrations, baseline detection, idempotent additive migration, index management, WAL-safe backup, startup/CLI/update integration | DB_MIGRATION v1 (new), SCHEMA_MANIFEST v1 (new) | all agents (DB consumers) | HIGH | VERIFIED |
| CHG-0001 | Hermes-Behavior | TASK-2 | Behavioral/anomaly intelligence: wire BehaviorDetectionEngine into IntelligenceWorker; add evidence-based detectors; versioned idempotent persistence; report truth-states (NO_DATA/CLEAR/FLAGS_FOUND/ANOMALIES_FOUND) + coverage | BEHAVIOR_ANALYSIS v1 (new), ANOMALY v1 (new) | Hermes-Accounting (reporting), Hermes-TradeLifecycle (TASK-3) | MEDIUM | VERIFIED |
| CHG-0006 | Hermes-DBHygiene | TASK-11 | Database hygiene worker: inventory/classification matrix, deterministic duplicate+orphan detection, retention engine, archive-before-delete with hashes, bounded executor, journal, verification, CLI nexus db hygiene *, worker state | DATABASE_HYGIENE v1 (new), RETENTION_POLICY v1 (new) | all DB consumers | MEDIUM | VERIFIED |
| CHG-0015 | Hermes-70D-Integration (TASK-2) | TASK-02-70D-INTEGRATION | 70D Liquidity integration + UI/runtime control plane: scalp_v4=70D schema, features/liquidity_runtime.py governor, /api/liquidity/* endpoints, canonical liquidity section in /api/status + live/state + SSE, Liquidity Intelligence UI panel + toggle, chart overlays from real pools, TEST-70D-01..28 + API suite | LIQUIDITY_RUNTIME v1 (new), LIQUIDITY_API v1 (new), FEATURE_SCHEMA_70D v1 (scalp_v4) | live_engine (info-only hook), web/server.py, Web/ | LOW | VERIFIED |
| CHG-0005 | Hermes-TASK1 (Performance Intelligence Data-Truth Auditor) | TASK-1 | Metric-truth repair (Performance Intelligence): MAE/MFE sign-convention normalization, MFE-capture portfolio-ratio semantics, fill-rate real denominator, funnel rejection re-tabulation (NO_TRADE+blocked_by), timestamp-cutoff normalization (ISO 'T' vs space), drawdown window labels (period vs 90D), model funnel new prediction_to_trade_rate | ACCOUNTING_SNAPSHOT v1 (extended: period_drawdown_pct, drawdown_window), TRADE_OUTCOME v3 (read), EXIT_CLASSIFICATION v2 (read) | Hermes-Accounting, Hermes-Behavior (reporting), Hermes-TradeLifecycle (TASK-3) | MEDIUM | VERIFIED |

```text
CHANGE-ID: CHG-0001
Agent: Hermes-Behavior
Role: Behavioral Intelligence / Anomaly Detection Forensic Engineer
Task: TASK-2
Scope: Behavioral + anomaly intelligence pipeline (detector invocation, evidence-based
       detectors, versioned idempotent persistence, truthful report states)
Affected files: src/nexus_scalp/intelligence/behavior.py, worker.py, store.py,
       adapters/database/audit_repository.py (schema add), reporting/engine.py,
       reporting/models.py, reporting/insights.py, reporting/telegram_format.py,
       reporting/__init__.py, application/live_engine.py (worker tick only),
       web/server.py (API contract), tests/unit/test_behavior_anomaly_intelligence_phase16.py
Affected functions/classes: BehaviorDetectionEngine.analyze, IntelligenceWorker._refresh_once,
       PerformanceReportEngine._stage_behavioral, compute_anomalies, formatters
Contracts touched: BEHAVIOR_ANALYSIS v1 (new), ANOMALY v1 (new)
Runtime paths touched: IntelligenceWorker background tick (30s cadence, off hot path)
Owners affected: Hermes-Accounting (reporting contract), Hermes-TradeLifecycle (TASK-3)
Risk: MEDIUM
Dependencies: BUG-081/082 ledger/exit normalization; TASK-1; TASK-3 (sequential consumer)
Required tests: TEST-BHV-01..20 (tests/unit/test_behavior_anomaly_intelligence_phase16.py)
Status: MERGED
```

## Template

```text
CHANGE-ID: CHG-XXXX
Agent: <name>
Role: <role>
Task: <task>
Scope: <scope>
Affected files: <files>
Affected functions/classes: <fns>
Contracts touched: <contracts>
Runtime paths touched: <paths>
Owners affected: <owners>
Risk: LOW | MEDIUM | HIGH | CRITICAL
Dependencies: <deps>
Required tests: <tests>
Status: PROPOSED | IMPLEMENTING | VERIFIED | READY_FOR_REVIEW | MERGED | REJECTED
CHANGE-ID: CHG-0016
Agent: Hermes-LiqOptAgent6
Role: Liquidity Optimization Engineer
Task: TASK-06-70D-LIQUIDITY-OPTIMIZATION
Scope: Liquidity Intelligence 10D (indices 60..69 scalp_v4 / 50..59 scalp_liquidity_v1) forensic optimization: baseline measurement (real M5),
       redundancy analysis vs Base 50D + News, bounded parameter search (EQH/EQL tolerance, confluence cutoff, sweep detection thresholds,
       HTF weights, displacement window) with temporal train/validation/OOS discipline, golden dataset A/B comparison, TEST-LIQ-OPT-01..28,
       algorithm versioning (liquidity-v1 -> liquidity-v1.x). NO production activation: candidate-only.
Affected files: src/nexus_scalp/features/liquidity_engine.py (parameterization, ONLY when evidence-justified), docs/LIQUIDITY_70D_OPTIMIZATION_REPORT.md,
       docs/LIQUIDITY_70D_IMPLEMENTATION_MAP.md, tests/unit/test_liquidity_optimization_phase19.py (new), agents/* registries
Contracts touched: none (50D Base + News unchanged; liquidity family contract preserved; version bump only)
Owners affected: Hermes-Parity (TASK-3), Hermes-ModelValidation-04, Hermes-Shadow70D, Hermes-LiquidityResearch (TASK-7)
Risk: LOW (candidate-only; no live switch; no execution path)
Dependencies: TASK-01..05 70D series landed; TASK-03 parity in flight
Status: IN_PROGRESS
```

## Notes
- Substantial tasks also get a TASK-ID in `agents/taskboard.md` and a handoff in `docs/agent_handoffs/`.
- Reference CHANGE-ID in PRs/issues alongside BUG-NNN / TASK-ID (contract §42).
CHANGE-ID: CHG-0019
Agent: Hermes-Forensic-70D-UI
Role: Debug UI / Runtime Observability Engineer
Task: TASK-70D-DEBUG-CONSOLE
Scope: Debug tab upgraded into full 70D runtime intelligence console.
       One canonical /api/debug/state snapshot (18 sections) rendered by
       the UI — the frontend never computes trading intelligence.
       Registry-driven 70D feature matrix (schema_contract scalp_v3),
       contract validation (70D CONTRACT BROKEN / MODEL CONTRACT INVALID
       incl. actual_classes=128 regression), confidence pipeline, policy
       gate trace, risk/exposure/execution/positions/exit forensics,
       liquidity context, news, workers, database, caches, chart, SSE
       diagnostics, snapshot capture/compare/diff. TEST-DEBUG-01..32.
Affected files: src/nexus_scalp/web/debug_snapshot.py (new),
       src/nexus_scalp/web/server.py (/api/debug/state + snapshots +
       compare + SSE diagnostics), src/nexus_scalp/application/
       live_engine.py (_last_model_input_tensor stash), Web/index.html,
       Web/app.js (Debug tab rebuild), tests/unit/
       test_debug_snapshot_phase20.py (new, 36 tests),
       docs/DEBUG_70D_FORENSIC_UPGRADE_FINAL.md (new),
       docs/agent_handoffs/2026-08-19_Hermes-Forensic-70D-UI_debug-console.md
Contracts: DEBUG_SNAPSHOT v1 (canonical /api/debug/state payload),
       FEATURE_SCHEMA_70D v1 (scalp_v3 registry-driven rendering)
Status: MERGED
```text
CHANGE-ID: CHG-0020
Agent: Hermes-SecurityHardening
Role: CodeQL Security Alert Remediation
Task: BUG-113 (16 GitHub code-scanning alerts: 12 py/exception-information-exposure, insecure temp file, clear-text storage, URL sanitization)
Scope: All exception handlers in web/server.py diagnostics/debug/db-status/deploy-gate/SSE paths now return sanitized _err() envelopes and generic codes; full detail only in server logs. Incident report writes wrapped in restrictive umask (0o077; no-op on Windows). Worker-stall probe test uses TemporaryDirectory. Git-remote URL test uses host-boundary urlsplit check. incidents/__init__ __all__ completed (4 constants).
Affected files: src/nexus_scalp/web/server.py, src/nexus_scalp/incidents/reports.py,
       src/nexus_scalp/incidents/__init__.py, tests/unit/test_incident_response_task12.py,
       tests/unit/test_git_surveillance_task13.py, agents/bugs.md (BUG-113)
Affected functions/classes: get_db_hygiene, get_diagnostics_incidents,
       get_diagnostics_incident, get_diagnostics_health, get_diagnostics_lineage,
       get_diagnostics_forensics, get_diagnostics_incident_report,
       get_diagnostics_incident_zip, get_diagnostics_search, get_debug_state,
       get_db_migration_status, get_forensic_health, sse_telemetry_stream,
       write_incident_reports (+_restrictive_umask), incident package __all__
Contracts touched: NONE (response shapes unchanged for success paths; error envelopes
       already standardized via safe_error_payload; DB_MIGRATION_FAILED /
       FORENSIC_ENGINE_UNAVAILABLE error codes keep their previous string values)
Runtime paths touched: /api/diagnostics/*, /api/db/hygiene, /api/db/status,
       /api/debug/state, /api/forensics/health, /api/ticks/stream error frame
Owners affected: TASK-12 incidents (Hermes-IncidentResponse) — error envelope now
       sanitized; TASK-70D-DEBUG-CONSOLE (Hermes-Forensic-70D-UI) — debug snapshot
       reason is generic code; BUG-110 SSE diagnostic — field names preserved
Risk: LOW — error responses change from raw-exception text to generic codes;
       success-path payloads unchanged; detail remains in existing structured logs
Dependencies: BUG-110 SSE canonical_json, TASK-12 incidents store/reports
Required tests: tests/unit/test_incident_response_task12.py,
       tests/unit/test_git_surveillance_task13.py (87 passed)
Status: MERGED
```
```text
CHANGE-ID: CHG-0021
Agent: AGENT-12
Role: Continuous Forensic Monitoring & Production Safety Engineer
Task: TASK-12-POST-70D-MONITORING
Scope: Canonical deploy gate (deploy_gate.py, DEPLOY_POLICY, fail-safe
       FORENSIC_ENGINE_UNAVAILABLE, evidence JSON), beforePush.sh/ps1 step
       5/5, experience-gap forensics (first-divergence, s18 taxonomy,
       defect-rate semantics - corrects CHECK-ACC-04 misattribution), news
       source classification (s14 taxonomy, 200-but-wrong proven), liquidity
       golden-reference freeze (LIQUIDITY_70D_GOLDEN_BASELINE.json@4455874),
       dual-registry governance + fingerprint cross-verify (CHECK-GOV-01/02),
       bounded Telegram periodic report (config-driven, dedup), trend
       analysis, CLI forensic --deploy-gate/--trend/--gap/--report,
       TEST-POST70D-01..28
Affected files: forensics/{deploy_gate,experience_gap,news_sources,
       telegram_report,trend}.py (new), forensics/{checks,engine,
       references,__init__}.py (extended), cli/main.py, beforePush.sh/ps1,
       configs/base.yaml (forensic_report), docs/POST_70D_DEPLOY_GATE.md +
       TASK-12 final + anomaly audit + performance, tests/
Contracts touched: DEPLOY_GATE v1 (new), EXPERIENCE_GAP v1 (new),
       NEWS_SOURCE_CLASSIFICATION v1 (new)
Runtime paths touched: none (read-only monitoring; CLI/API/worker only)
Owners affected: TASK-8 governance (stale champion rows), Hermes-News (200-but-wrong)
Risk: LOW
Dependencies: TASK-11 monitoring engine, 70D golden baseline
Required tests: TEST-POST70D-01..28, TEST-MONITOR-01..36
Status: READY_FOR_REVIEW
```

```text
CHANGE-ID: CHG-0019
Agent: Hermes-CI-Isolation
Role: CI / GitHub Actions Pipeline Engineer
Task: TASK-CI-ISOLATION
Scope: GitHub Actions CI isolation & test branch. Audited all 4 workflows
       (ci/release/docker/security). ci.yml split: 'quality' (ruff/mypy/
       unit+coverage) runs on every push+PR to main|develop; new 'heavy-ci'
       matrix (integration / e2e-playwright / research-backtest / model-
       validation) runs ONLY on the ci-tests branch or manual dispatch
       (inputs.full=true). security.yml: push trigger replaced by ci-tests
       branch + PR + weekly schedule + dispatch. docker.yml + release.yml:
       triggers preserved (comment-only). concurrency groups added to
       ci.yml (already present) + security.yml. ci-tests branch created
       from origin/main. Actionlint-validated (all files pass; release.yml
       pre-existing ::set-output deprecation warning at line 247 NOT
       modified — out of scope).
Affected files: .github/workflows/ci.yml (rewritten), .github/workflows/
       security.yml (rewritten), .github/workflows/docker.yml (comment
       only), agents/change_control.md, agents/taskboard.md,
       agents/repository_state.md (append-only rows)
Affected functions/classes: NONE in src/ (workflow YAML only)
Contracts touched: CI_TRIGGER_POLICY v1 (heavy CI only on ci-tests /
       dispatch; security weekly+PR; docker/release unchanged)
Runtime paths touched: NONE (no application code, no DB, no hot path)
Owners affected: Hermes-Release (release.yml untouched), Hermes-DevOps
       (workflow owners)
Risk: LOW. Heavy CI no longer runs on every push — intentional (objective).
       PR validation + security PR scans + release triggers preserved.
Dependencies: none
Required tests: actionlint .github/workflows/*.yml (0 errors on changed
       files), YAML parse, branch filter cross-check
Status: MERGED
```

```text
CHANGE-ID: CHG-0022
Agent: Hermes-CI-Reporting
Role: CI / GitHub Actions Pipeline Engineer
Task: TASK-CI-REPORTING
Scope: Unified CI results system (ci-results/) for the quality job incl.
       ruff JSON + lint.txt, format.txt, mypy text + junit, pytest
       junit.xml + log + cobertura coverage.xml + htmlcov, run-info/
       (metadata.json, summary.md, manifest.json, SHA256SUMS.txt,
       per-check status json, secrets-present.json), $GITHUB_STEP_SUMMARY,
       one canonical artifact per run (30-day retention), final gate
       preserving real failure exits. Heavy-ci matrix arms (integration /
       e2e / research-backtest / model-validation) each produce their own
       artifact; aggregate job merges into ONE canonical ci-results
       artifact. docs/ci-secrets.md added. release.yml ::set-output
       deprecation fixed via $GITHUB_STEP_SUMMARY (arm64-report job).
       Scripts: scripts/ci/make_ci_results.py (authoritative result
       pipeline, validated end-to-end locally).
Affected files: .github/workflows/ci.yml (extended: heavy-ci + aggregate +
       unified reporting), .github/workflows/release.yml (::set-output ->
       GITHUB_STEP_SUMMARY), scripts/ci/make_ci_results.py (new, from
       parallel agent — adopted + verified), docs/ci-secrets.md (new),
       agents/change_control.md, agents/taskboard.md, agents/repository_state.md
Affected functions/classes: NONE in src/ — workflow YAML + CI scripts only
Contracts touched: CI_RESULTS v1 (canonical reporting layout), CI_TRIGGER_POLICY
       v1 (unchanged: heavy CI only on ci-tests/dispatch)
Runtime paths touched: NONE (no application code)
Owners affected: none (release workflow triggers unchanged)
Risk: LOW. Reporting-only additions; failure semantics preserved (final
       gate fails on any check failure; artifacts still uploaded on failure).
Dependencies: none
Required tests: actionlint all workflows (0 errors); make_ci_results.py
       exercised end-to-end locally (init/check/summary/manifest/checksums);
       release.yml diff review; live GitHub run verification pending
Status: VERIFIED
```

```text
CHANGE-ID: CHG-0023
Agent: Hermes-CI-Lint
Role: CI / lint gate engineer
Task: TASK-CI-LINT-FIX
Scope: Fix GitHub Actions quality gate (ruff check/format) broken by
       unpinned ruff (>=0.2.0 -> latest 0.16.3 flagging 141 lint errors
       across scratch/ probes). Pin ruff==0.16.3 in pyproject dev extras
       (reproducible CI), fix all lint errors in tracked files (import
       sorting/placement, unused imports, semicolon statements, percent
       format -> f-strings, B023 lambda loop-var binding, RUF034
       redundant hasattr, PLR0124 psi!=psi -> math.isnan, PLW2901 loop
       var, B007 unused loop vars, PLW1510 subprocess check=False,
       zip strict=, UP017 datetime.UTC), ruff-format all tracked files.
       Parallel-agent WIP files (Web/, src/ live_engine etc.) NOT
       touched; scripts/ci/*, tests/conftest.py, 3 test files formatted
       to keep `ruff format --check .` green repo-wide.
Affected files: pyproject.toml (ruff pin), scratch/*.py (~40 probes),
       scripts/ci/{make_ci_results,collect_results}.py,
       tests/conftest.py, tests/unit/test_70d_parity_task3.py,
       tests/unit/test_debug_snapshot_phase20.py,
       tests/unit/test_incident_response_task12.py,
       tests/unit/test_release_*_phase19.py (+7 format-only test files),
       agents/{change_control,taskboard}.md
Affected functions/classes: NONE in src/ (scratch/scripts/tests/lint only)
Contracts touched: none (no src/ behavior change)
Runtime paths touched: NONE
Owners affected: all scratch/ owners (format/lint normalization only)
Risk: LOW. Lint/format-only; no runtime behavior change. ruff pinned to
       the CI-verified version so local == CI.
Dependencies: none
Required tests: ruff check . (clean), ruff format --check . (clean),
       mypy src (clean), pytest tests/unit
Status: VERIFIED
```
```text
CHANGE-ID: CHG-0024
Agent: Hermes-SecurityAudit
Role: GitHub Security & Quality audit engineer
Task: TASK-SEC-AUDIT
Scope: Residual GitHub code-scanning batch (BUG-121): webfonts route
       rewritten to listing-match (no user input in path expressions),
       debug_snapshot section handlers + research health return stable
       error markers (exception detail server-side via logger), incident
       reports gain value-level secret-shape redaction. All 4 workflow
       files' third-party actions pinned to immutable commit SHAs
       (trivy-action was on @master). Dependabot vulnerability alerts +
       automated security updates enabled at repo level (were disabled).
       ruff exclude added for scratch/ + _cleanup_hold_20260819 so
       parallel-agent probe WIP cannot break CI. pip-audit on the venv:
       no known vulnerabilities. No trading/risk/model code touched.
Affected files: src/nexus_scalp/web/server.py, web/debug_snapshot.py,
       incidents/reports.py, research/store.py, tests/unit/
       test_frontend_assets_phase14.py (absorbed in 4f45a26),
       test_debug_snapshot_phase20.py, test_incident_response_task12.py,
       tests/integration/test_research_api.py, pyproject.toml,
       .github/workflows/{security,ci,docker,release}.yml,
       agents/{bugs,change_control}.md
Affected functions/classes: serve_fa_webfont (server.py); 9 snapshot
       section handlers + _safe (debug_snapshot.py); mask_secrets +
       _SECRET_VALUE_RE (reports.py); research_health_summary
       (research/store.py)
Contracts touched: WEB_FONT_SERVING v2 (listing-match, 404 semantics
       preserved), DEBUG_SNAPSHOT error contract (stable reason codes,
       exception text removed from wire), INCIDENT_REPORT_MASKING v2
       (value-level redaction), CI action pinning policy (SHA-only)
Runtime paths touched: web webfont serving, /api/debug/state,
       /api/research/health, incident report writes (all error paths
       only - happy paths byte-identical)
Owners affected: none (error-path contract changes only; UI reads
       available/reason fields which remain)
Risk: LOW. Error paths never change trading behavior; webfonts serves
       the same 8 real files; masking only redacts secret shapes.
Dependencies: none
Required tests: ruff check . (clean), ruff format --check (clean on
       src+tests+scripts), mypy src (clean), pytest unit + research API
       (targeted suites), 153 tests in the 4 touched suites pass
Status: VERIFIED
```

```text
CHANGE-ID: CHG-0025
Agent: Hermes-CI-Ready
Role: CI / GitHub Actions Pipeline Engineer
Task: TASK-CI-READY
Scope: Enterprise GitHub Actions modernization — workflows only, NO
       application code. Records pre-existing CI reliability findings
       (order-dependent flakiness, parallel-WIP interactions,
       environment-dependent failures) as SEPARATE findings with CI-side
       remedies (sharding, isolation, retries, timeouts) — never by
       weakening tests. Deliverables: timeouts/retries/concurrency
       hardening, artifact orchestration + verification (checksums/SBOM),
       test result aggregation, GitHub API-safe behavior, summaries,
       Telegram HTML observability.
Affected files: .github/workflows/*.yml, scripts/ci/*, docs/CI_RELIABILITY_FINDINGS_2026-08-19.md, agents/registries
Contracts touched: CI_RESULTS v1 (extended), CI_TRIGGER_POLICY v1 (extended)
Risk: LOW (workflows/scripts only; no src/ runtime paths)
Dependencies: TASK-CI-ISOLATION, TASK-CI-REPORTING
Required tests: actionlint on all workflows, YAML parse, dry-run artifact flow
Status: IN_PROGRESS
```

```text
CHANGE-ID: CHG-0026
Agent: Hermes-CI-Telegram
Role: CI/CD Observability & Notification Engineer
Task: TASK-CI-TELEGRAM
Scope: Enterprise Telegram CI/CD observability layer (spec: HTML-format,
       split/HTML-safe, secret-redacted, file-uploading, isolated).
       New src/ modules: observability/telegram_html.py (central HTML
       renderer + esc() + split_html_message with tag-safe splitting,
       Unicode/Persian-safe), observability/telegram_transport.py
       (sendDocument upload + captions + redact_secrets for text/files),
       observability/ci_telegram_reporter.py (orchestrator reading
       ci-results/run-info, junit wrapper, coverage; chat-id resolution
       TELEGRAM_CHAT_ID > NEXUS_TELEGRAM_ADMIN_ID). CLI
       scripts/ci/telegram_notify.py (run-started/run-finished/
       test-summary/artifacts/release-*/push/pr; ALWAYS exit 0 so CI
       never fails on Telegram). ci.yml wired: Telegram - CI started/finished
       steps (advisory, continue-on-error). docs/ci-telegram-operations.md
       (config, chat-id how-to, retry/timeout/redaction policy),
       docs/ci-secrets.md (+TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID rows).
       Tests: tests/unit/test_telegram_html.py (36) +
       test_ci_telegram_reporter.py (20) — HTML escaping, malformed
       values, long-message splitting, Persian/Unicode, redaction,
       file uploads, missing/oversized files, chat-id resolution
       (incl. USER_ID), failure isolation.
       Enterprise wiring: USER_ID accepted as Telegram destination
       (fallback < TELEGRAM_CHAT_ID); release.yml concurrency guard
       (per-tag, no cancel), Telegram release started/gates/finished
       steps + POST-RELEASE VERIFICATION (API re-fetch: tag match +
       assets present); docker.yml concurrency + 40m timeout;
       security.yml Telegram security result step; CLI gains
       'security' subcommand.
Affected files: src/nexus_scalp/observability/{telegram_html,telegram_transport,
       ci_telegram_reporter}.py (new), scripts/ci/telegram_notify.py (new),
       .github/workflows/ci.yml (telegram env + 2 notify steps),
       docs/{ci-telegram-operations,ci-secrets}.md,
       tests/unit/{test_telegram_html,test_ci_telegram_reporter}.py,
       agents/{change_control,taskboard}.md
Contracts touched: TELEGRAM_OBSERVABILITY v1 (new; canonical HTML message
       vocabulary + correlation id NEXUS-CI-<run>-<sha4>); CI_RESULTS v1
       (consumed: run-info/*.json, junit.xml, coverage.xml)
Runtime paths touched: NONE (src/ additions only; no trading/execution/ML
       code modified; existing TelegramNotifier untouched)
Owners affected: TASK-CI-READY (CHG-0025) — complementary: this is the
       library+CLI+tests; CHG-0025 keeps workflow hardening
Risk: LOW. All new modules; CI fails never on Telegram errors; messages
       redacted before send; no secrets referenced in logs/artifacts
Dependencies: TASK-CI-REPORTING (ci-results), CHG-0024 (secrets doc)
Required tests: pytest tests/unit/test_telegram_html.py
       tests/unit/test_ci_telegram_reporter.py (55 passed); ruff check +
       format on all new files; actionlint on ci.yml
Status: READY_FOR_REVIEW
```

```text
CHANGE-ID: CHG-0023
Agent: Hermes-CI-Reporting
Role: CI / GitHub Actions Pipeline Engineer
Task: TASK-CI-REPORTING (follow-up)
Scope: Live-verification fixes on the unified CI results system:
       1. heavy-ci + aggregate jobs now use if: always() && (ci-tests ||
          dispatch full=true) so a failing quality job no longer silently
          skips the entire heavy matrix (verified live on run 32283621817:
          all 4 heavy arms executed despite quality failure).
       2. Aggregate job merge fixed: download-artifact extracts artifact
          CONTENTS at artifacts/<name>/ (no ci-results/ wrapper); the loop
          now copies each arm's contents into ci-results/heavy/<suite>/ and
          quality(-CI) into the top level, then make_ci_results.py summary +
          manifest regenerate over the merged tree (verified live on
          run 32290418115: aggregate upload succeeds).
       3. Artifact naming made collision-free: quality uploads as
          ci-results-quality-..., arms as ci-results-<suite>-..., aggregate
          as ci-results-<workflow>-...-aggregate (parallel agent's naming
          adopted where present).
Affected files: .github/workflows/ci.yml (job ifs + aggregate merge loop +
       artifact names)
Affected functions/classes: NONE in src/ — workflow YAML only
Contracts touched: CI_RESULTS v1 (aggregate layout: quality top-level +
       heavy/<suite>/), CI_TRIGGER_POLICY v1 (unchanged)
Runtime paths touched: NONE
Owners affected: none (actions SHA-pinning landed separately via
       Hermes-SecurityAudit 4b01a3e/9fc0972)
Risk: LOW (workflow-only; failure semantics preserved)
Dependencies: CHG-0022 (unified results)
Required tests: actionlint clean; merge layout simulated locally; live
       GitHub runs verified (quality artifact + per-arm artifacts + aggregate)
Status: VERIFIED
```
## CHG-0027 — Client CLI Update Engine v2: exact-release identity, checksum-asset digests, stage-differentiated commands (TASK-UPDATER-02, 2026-08-20)

**Status:** VERIFIED
**Agent:** Hermes-UpdateCLI
**Scope:** src/nexus_scalp/release/updater.py, src/nexus_scalp/cli/main.py, docs/RELEASE.md, tests/unit/test_release_update_phase17.py
**Change:**
- Release selection now locks an exact identity (release_id, tag, commit_sha, published_at) before download; draft releases and REVOKED markers are never eligible.
- SHA-256 digests resolved from the release's own checksum assets (GitHub `gh-md5`-style algorithm=gzip uploads) with per-asset manifest/checksum-file cross-verification; a release without a resolvable digest is SECURITY_BLOCKED.
- `--include-prerelease` / `--allow-downgrade` explicit opt-ins; `--force` remains LIVE-quiesce authorization.
- Retry/backoff (429 Retry-After honored), resumable-download hash now computed over full bytes, ETag/If-None-Match metadata cache + `--force` refresh, offline -> NETWORK_UNAVAILABLE.
- Installed-release local state written post-install; `nexus update check|latest|download|install|verify`, `nexus release info`, unified exit-code table.
**Invariants touched:** UPDATE PROTOCOL v2 (additive; all prior UPDATE PROTOCOL v1 semantics retained).
**Tests:** TEST-UP-36..60 in tests/unit/test_release_update_phase17.py.
**Risk:** additive; no change to LIVE-safety, backup, migration or rollback contracts.
```text
CHANGE-ID: CHG-0028
Agent: Hermes-LogArchitect
Role: Observability / Structured Logging Architecture Engineer
Task: Master Structured Logging & Organized Log Storage brief
Scope: Centralized severity-split date-organized logging engine
       (src/nexus_scalp/observability/logging.py): logs/<severity>/YYYY/MM/
       YYYY-MM-DD.log tree, ISO-8601+03:30 timestamps, stable event names +
       category + NEXUS-* error codes, correlation/run/generation/strategy
       context fields, daily + size rotation (part-NNN, zero-loss), per-severity
       retention + hourly prune (archive never auto-deleted), key-based +
       high-entropy redaction (BUG-121), ANSI-free plain-text error files with
       full stack traces, process-wide write lock. Call sites: launcher,
       LiveEngine.start(), train_model. Tests: tests/unit/test_logging.py
       (14 tests: routing/stack/redaction/correlation/rotation/retention).
Affected files: src/nexus_scalp/observability/logging.py (rewrite),
       NexusTradingForexBot.py, src/nexus_scalp/application/live_engine.py,
       src/cli/train_model.py, tests/unit/test_logging.py (rewrite),
       docs/agent_handoffs/Hermes-LogArchitect-structured-logging.md
Affected functions/classes: configure_logging (log_file_path now base dir +
       retention_days), get_logger (unchanged), log_event (new),
       bind_correlation_id (new), DatedRotatingFileHandler (new),
       _LevelMatchFilter (new), _prune_old_logs (new)
Contracts touched: OBSERVABILITY v2 (severity-split layout), INV-001 honored
       (no sync I/O added to tick path beyond the existing throttled sink)
Runtime paths touched: boot path (launcher/live_engine.start/train_model)
Commits: 7c19a34, 208eebe, df5c1e2, efa2afa, 96d7c82 (local)
Status: VERIFIED (14 tests PASS; acceptance run produced info/warning/error/
       critical files with stacks, redaction, correlation; rotation zero-loss;
       retention verified) -> READY_FOR_REVIEW
```

CHANGE-ID: CHG-0019
Agent: Hermes-LiquidityCompat
Role: Liquidity-Model Compatibility Engineer
Task: BUG-123 — contract-based model compatibility for Liquidity Intelligence
Scope: liquidity_runtime.py (resolve_model_compatibility engine, governor
       model contract + compatibility_contract + report contract sections),
       Web UI (Model Contract/Reason cells, State Revision row), tests
       (test_liq_bug123_01..16), proof artifact (liq70_proof scalp_v3 70D)
Affected files: src/nexus_scalp/features/liquidity_runtime.py,
       Web/index.html, Web/app.js (absorbed via parallel commit),
       tests/unit/test_liquidity_runtime_integration_phase18.py,
       tests/integration/test_liquidity_api.py,
       scratch/fix_70d_proof_artifact.py (new artifact builder)
Affected functions/classes: resolve_model_compatibility (contract engine),
       model_schema_family, build_model_compatibility_contract,
       LiquidityGovernor._model_contract/compatibility_contract/model_compatibility,
       report() (liquidity_contract + snapshot_coherence_revision),
       snapshot_payload (per-value normalization/validity)
Contracts touched: LIQUIDITY_70D v1 (reason vocabulary + contract descriptor),
       INV-022 (new), schema_contract reused
Runtime paths touched: /api/liquidity/state, /api/liquidity/features,
       /api/status liquidity section, Debug console liquidity section (all
       derive from governor.report()); NO model inference path changed
Owners affected: our liveness of the 70D candidate (TASK-04/05/09), UI
       consumers of the reason string (docs/LIQUIDITY_UI_FORENSIC_*)
Risk: LOW. The compatibility verdict for the CURRENT production champion
       (50D) remains BLOCK (truthful); only the reason + contract detail
       changed. No padding/truncation anywhere.
Dependencies: schema_contract (canonical 70D), ChampionManager (BUG-118)
Required tests: test_liq_bug123_01..16 (all pass; suites 73 passed)
Status: VERIFIED

```text
CHANGE-ID: CHG-0029
Agent: Hermes-DBHygiene
Role: Database Hygiene / Runtime Data Integrity Engineer
Task: TASK-22-DB-HYGIENE-RUNTIME
Scope: Continuous runtime database hygiene engine on top of the TASK-11
       worker. New hygiene/ modules: quarantine.py (DataQuarantine store,
       MOVE->MARK->REPORT, restore/resolve, provenance), consistency.py
       (read-only rule engine: trade/ledger/dataset/news validation),
       index_health.py (missing/duplicate/unused index advisory +
       QUERY_HEALTH_REPORT), report.py (DATABASE_HYGIENE_INITIAL_REPORT +
       cycle telemetry + Telegram report text), hygiene_runtime.py
       (RuntimeCleanupScheduler conductor: config cadence, first-run audit,
       deep maintenance, telegram cooldown). Config: database_hygiene
       section in configs/base.yaml + DatabaseHygieneConfig/RetentionsConfig.
       Wiring: live_engine tick loop (scheduler replaces bare worker),
       /api/db/hygiene (runtime + quarantine), CLI nexus db hygiene
       health|cleanup --dry-run|--deep|quarantine, Web Database Health Panel
       (fixes previously-dead loadHealthPanel button, BUG-119 pattern).
Affected files: hygiene/{quarantine,consistency,index_health,report,
       hygiene_runtime}.py, configuration/config.py, configs/base.yaml,
       application/live_engine.py, cli/db_commands.py, web/server.py,
       Web/{index.html,app.js}, tests/unit/test_database_hygiene_task11.py,
       docs/agent_handoffs/Hermes-Hygiene-Runtime-TASK22.md
Contracts touched: DATABASE_HYGIENE v2 (runtime scheduler + quarantine +
       consistency + index health), RETENTION_POLICY v1 (unchanged)
Risk: LOW (runtime cycles default dry_run=True; deletes require
       apply_deletes + non-LIVE execution; schema changes still go through
       TASK-10 migrations; index findings are advisory only)
Dependencies: TASK-11 hygiene worker, TASK-10 migrations, TASK-12 telegram
Required tests: TEST-HYG-37..48 (scheduler, first-run audit, quarantine,
       consistency, index health, dry-run, protected data, telemetry, budget)
Status: VERIFIED (49 tests in file PASS; ruff+format+mypy clean)

## CHG-0028 — EXEC trace-id observability: single execution_id across signal→dispatch→broker (BUG-124, 2026-08-20 Hermes-Forensic-ExecAudit)

Change: Add `TradeProposal.execution_id` (EXEC-YYYYMMDD-HHMMSS-xxxxxx) stamped once per evaluation in SignalPolicy.evaluate_probabilities and carried into every proposal; `[EXEC_TRACE]` structlog line per finalized decision; dispatch_order embeds `| exec=<id>` in audit_orders.reason; new read-only endpoint GET /api/debug/trace/{execution_id}.
Scope: src/nexus_scalp/domain/models.py, src/nexus_scalp/signals/policy.py, src/nexus_scalp/execution/order_manager.py, src/nexus_scalp/web/server.py, tests/unit/test_policy.py, tests/unit/test_domain_models.py
Why: Forensic audit found NO positions opened (stacked filter deadlock). The trace id is the observability prerequisite to attribute every future rejection to its exact gate — without weakening any safety gate.
Migration: none (additive optional field; DB schema unchanged; reason-string embeds the id).
Verification: VERIFIED — 12 policy/domain tests + 38 debug-snapshot tests PASS; live probe `[EXEC_TRACE] execution_id=EXEC-20260820-002033-d783c9 action=NO_TRADE blocked_by=ASYMMETRIC_RR_LIMIT stage=STANDARD_EVAL`.
Risk: minimal — observability only; no decision/behavior change.

## CHG-0029 — Stash reconciliation: DB-portability settings canonicalization + logging-test fix + DDL literal porting (2026-08-20 Hermes-StashMerge)

Change: (1) settings/service.py set_postgres_config/set_database_provider canonicalized to PG_CONFIG_SETTING_KEY json row + PG_PASSWORD_SECRET_KEY secret-store + provider/domain fields, aligned with database/config.py readers (verified end-to-end load_database_config -> resolve_password); (2) database/config.py load_database_config accepts dict OR string for the persisted json row; (3) ddl_port.py normalizes SQLite double-quoted string literals to single-quoted for PG (identifiers preserved); (4) test_logging.py event-name parser fixed (was asserting wrong token from the severity-split renderer).
Scope: src/nexus_scalp/settings/service.py, src/nexus_scalp/database/config.py, src/nexus_scalp/database/ddl_port.py, tests/unit/test_logging.py, tests/unit/test_settings_subsystem_bug072.py, tests/unit/test_database_portability.py
Why: HEAD persisted PG config per-key (database.postgres.*) while config.py/health.py read the canonical key — `nexus db postgres set` data was never consumed (host fell back to localhost). test_logging had 4 failing routing tests from an outdated token split. Ported DDL could break under PG for double-quoted literals.
Migration: none — old per-key rows are ignored (fresh write required), matching prior behavior; no DB schema change.
Verification: VERIFIED — end-to-end probe (set -> load -> resolve password == S3cret!), test_settings_subsystem_bug072 22 passed, test_database_portability TestDdlPorting 7 passed, test_logging 13 passed; ruff + mypy clean.
Risk: low — additive alignment; heuristic literal classifier documented; settings writes are CLI/API only (not hot path).

## CHG-0030 — Isolated strategy research store (SQLite+PostgreSQL) (2026-08-20 Hermes-StrategyIsolation)

Change: generated-strategy research memory moved OUT of the audit DB into a dedicated portable store.
(1) NEW src/nexus_scalp/strategies/research_store.py — StrategyResearchStore over the DatabaseDriver
abstraction (SQLite artifacts/strategies.db default, PostgreSQL via NSE_PG_TEST_URL / config): 8 tables
(7 factory tables + strategy_research_meta), provider-portable DDL via ddl_port.port_create_table,
idempotent ensure_schema, explicit-commit writes (driver auto-commit gap: SQLite execute/upsert without
an explicit connection rolls back silently — verified). (2) factory/store.py dual-backend dispatch: every
function accepts either AuditRepository (legacy audit queue) or StrategyResearchStore (isolated);
_resolve_backend duck-types on 'driver'. (3) factory/orchestrator.py StrategyFactory gains store= param +
_research_backend property; 27 store-function call sites route through it; strategy_registry reads stay on
the audit repo (shared validation truth). (4) live_engine.py opens the isolated store at startup
(fallback to audit queue on failure); factory wired with store=_strategy_store. (5) factory_routes.py UI
reads through factory._research_backend. (6) provider.py 'strategies' domain registered.
Scope: new src/nexus_scalp/strategies/research_store.py, new tests/unit/test_strategy_research_store.py,
src/nexus_scalp/strategies/factory/store.py, orchestrator.py, src/nexus_scalp/application/live_engine.py,
src/nexus_scalp/web/factory_routes.py, src/nexus_scalp/database/provider.py
Why: generated strategies are research memory, not trade truth — they must not grow inside the live audit
path. Audit DB stays small/fast for order/signal/ledger truth. Same store code runs on SQLite and PG.
Migration: none for existing DBs — audit_orders execution_id column already migrated by 474f7f2
(verified in error-log forensics: errors stopped at 09:50, engine restarted 09:59/10:04 on fixed code).
Verification: VERIFIED — 74 passed / 13 skipped (PG arm) across test_strategy_research_store (21),
test_database_portability (26), test_strategy_factory_phase22 (19), test_audit_db_growth_bug054 (12);
ruff check+format clean; mypy clean on all 7 files. Commit 318964e.
Risk: LOW — additive; audit-queue fallback preserved; strategy_registry unchanged.


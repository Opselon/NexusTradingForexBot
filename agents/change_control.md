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
Status: VERIFIED
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
Status: VERIFIED
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
Status: VERIFIED
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
Status: VERIFIED
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
Status: VERIFIED
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
Status: VERIFIED
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


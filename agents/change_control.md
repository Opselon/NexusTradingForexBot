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

## CHG-0030 — Market Structure & Liquidity Intelligence Engine (MSLIE): perception layer for AI models (2026-08-20 Hermes-MSLIE)

Change: New `src/nexus_scalp/mslie/` package — a Market Perception Engine converting raw OHLC/volume/spread into structured intelligence: MarketIntelligenceFeatureVectorV1 (versioned contract), adaptive swing detection (ATR thresholds, volatility-adjusted window, volume confirmation, reaction strength, timeframe weight), BSL/SSL liquidity map ranked LOW/MEDIUM/HIGH/EXTREME, stop-hunt detector (pool + violation + rejection/acceptance + displacement + follow-through; REVERSAL/CONTINUATION/UNCERTAIN), breakout quality (real vs fake), smart-money features (OB/FVG/displacement/inducement/premium-discount), bounded multi-month MarketMemory. Wired: LiveEngine bar-close hook (pure numpy, no I/O, INV-001/INV-002), GET /api/mslie/status + /api/mslie/features, mslie section in /api/debug/state, Debug tab 'Market Intelligence Engine' panel.
Scope: src/nexus_scalp/mslie/ (new), src/nexus_scalp/application/live_engine.py, src/nexus_scalp/web/server.py, src/nexus_scalp/web/debug_snapshot.py, Web/index.html, Web/app.js, tests/unit/test_mslie_phase22.py, tests/integration/test_mslie_api.py, scratch/mslie_validate.py
Why: The platform lacked a market perception layer — AI models had no structured knowledge of where important highs/lows exist, where liquidity sits, whether stops were hunted, whether structure changed, or the market regime. The engine is advisory/observability-first; the decision stays with strategy models / ScalpNet / execution / risk.
Migration: none — no DB changes, no feature-schema changes (INV-009 untouched: the 50D/70D contract is never altered).
Verification: VERIFIED — 28 unit tests + 4 API integration tests + 38 debug-snapshot tests PASS; ruff/format/mypy clean; historical validation probe (3 regimes) PASS; node --check Web/app.js OK.
Risk: LOW — pure perception, zero order authority, failure-isolated hooks; latency 10-20ms on 300 bars.

## CHG-0031 — DATABASE MANAGEMENT console: SSMS-style explorer, SQL console, API keys (2026-08-20 Hermes-DBConsole)

Change: new provider-abstracted web console under /api/db/console/* — databases (all DB files incl. settings, provider/size/status), refresh (auto-sync new DBs), tables+row counts, columns, paginated rows (500-row cap), read-only SQL query (SELECT/EXPLAIN/WITH/PRAGMA/VALUES only, placeholder translation for psycopg, single-statement, bounded timeout), ready-made quick SQL, and named API keys backed by the OS SecureSecretStore (masked only, never plaintext; reserved names rejected). UI: Database Explorer tree + table chips + row grid + SQL console + API Keys panel under the existing DATABASE MANAGEMENT tab. Same driver contract serves SQLite now and PostgreSQL after the active provider switch.
Scope: new src/nexus_scalp/web/db_console.py, src/nexus_scalp/web/server.py, Web/index.html, Web/app.js, Web/api_client.js (NX.api.del), tests/unit/test_database_portability.py (TestDbConsole*, 13 tests)
Why: user brief — SSMS-like Database Management UI (tables + ready buttons below Migration Workflow, open/see databases and rows, execute DB commands, API KEYS), future-proof for PostgreSQL, auto-sync when new DBs are added.
Migration: none — read-only surface; no schema change.
Verification: VERIFIED — 42 passed/10 skipped in test_database_portability.py (1.34s), ruff+mypy clean, live TestClient probes on real artifacts DBs, node --check on both JS files.
Risk: low — read-only; no hot-path changes.

## CHG-0032 - Large-file decomposition & debug-friendly architecture refactor (2026-08-31 Nexus-Main)

Change: structural decomposition of oversized source modules into responsibility-based domain packages with thin
compatibility facades. NO runtime behavior change (CLI/API/DB/config/model/logging/exit codes all preserved).
Phases: 1 forensic inventory (Researcher) -> 2 target-tree design (Main) -> 3 staged per-domain implementation (Coder,
one coherent commit per domain) -> 4 adversarial review (Reviewer) -> 5 QA regression gate (QA). Candidates ranked by
evidence, not LOC alone; order_manager.py and live_engine.py DEFERRED (convention-locked hot path, INV-001/INV-004).
Scope: TBD per Phase-1 inventory - expected src/nexus_scalp/cli/main.py, forensics/checks.py, release/updater.py,
adapters/database/audit_repository.py, web/server.py (P0 caution: REST/SSE/WS surface); docs/architecture/debug-map.md (new);
tests reorganized with source ownership. Parallel WIP in .github/workflows/release.yml + src/nexus_scalp/release/metadata.py
(BUG-166/174, release-identity owner) is EXCLUDED from this change.
Why: user-directed decomposition mission - lower cognitive load, clear ownership, debug-first navigation. Recent user-side
hunts (BUG-160/162/166/170-173) all landed in cli/release code where giant files hid failure modes.
Migration: none - no DB/schema/contract/feature changes permitted; public APIs preserved via facades; import graph checked
after each split (no cycles).
Verification: ruff check + ruff format --check + mypy src per step; targeted per-domain pytest; full critical suite via
beforePush before push; CLI --help/status real-execution smoke; behavior-preservation audit (before/after outputs).
Risk: MEDIUM - structure-only but touches CLI/release/forensics paths with fresh bug history (BUG-160/162/166/170-173);
mitigated by staged commits, facades, and regression gates per domain.
Status: PROPOSED -> IMPLEMENTING (Phase 1 dispatched)

## CHG-0032-A1 — SCOPE LOCK ruling (2026-08-31, user directive via Nexus-Main)

RULING: APPROVED WITH BINDING SCOPE NOTES for TASK-ARCH-DECOMP.

CURRENT PASS (approved order, Coder authorized):
1. src/nexus_scalp/cli/main.py — package split + facade (app_factory.py, update_cli.py, engine_boot.py, doctor.py,
   wizard.py, styling.py per Phase-1 contract); main.py re-exports app + console; module-level import-time app
   construction MUST remain (release/packaged_main.py + cli_shim.py import `app` at import; 12 importers).
2. src/nexus_scalp/forensics/checks.py — domain split; ALL check_* public names preserved via compatibility
   aliases (engine.py + tests alias the module as `C`); no check-semantics redesign (BUG-166 class rule: probe
   fallback semantics stay byte-identical while moving).
3. src/nexus_scalp/web/server.py — LAST, highest risk. EXISTING flat route convention: web/<domain>_routes.py
   (pattern already proven by factory_routes.py / news_intelligence_routes.py). NO web/routes/ nested package.
   Facade MUST re-export the verified surface: create_app, WEB_DIR, canonical_json, serialize_enums,
   safe_error_payload, log_web_error, new_request_id, _find_non_json_fields. The 28 API test files must NOT be
   modified to compensate for an incomplete facade.

DEFERRED — DO NOT TOUCH (no split/refactor/rename/formatting-only mutation/threshold change):
- signals/policy.py — foreign uncommitted WIP (BUG-054); separate controlled change later.
- execution/order_manager.py — hot-path convention lock (INV-004 OrderManager authority), BUG-105 init-order
  sensitivity, parallel-agent churn.
- application/live_engine.py — hot-path convention lock (INV-001), DI composition root, parallel-agent churn;
  BUG-105 engine-launch gate: tests/integration/test_engine_runtime_launch.py MANDATORY after every
  live_engine step in ANY future authorized task.

OUT OF SCOPE this pass (not authorized): adapters/database/audit_repository.py, release/updater.py,
web/debug_snapshot.py, and all other Phase-1 candidates (P2/P3 leave-alone set included).

Per-step gates (binding): py_compile; module-specific tests; ruff check; ruff format --check; mypy src.
Web step additionally: ALL 28 API test files + integration API suites run STANDALONE/SEQUENTIAL
(artifacts/audit.db collision — a combined run is NOT validation evidence). Critical-suite-only evidence is
NOT sufficient for the web step.

Baseline protection: before/after every step record git status / git diff --stat / git diff -- <targets> /
git diff --check; never absorb or revert parallel-owner changes; commit per responsibility with <AGENT>: title;
re-add own files immediately before commit (parallel restore --staged hazard).

Precedence note: user ruling supersedes researcher P0 ranking where they differ (checks.py P2 -> approved Step 2;
audit_repository.py P0 / updater.py P1 -> OUT OF SCOPE this pass).
Status: IMPLEMENTING (Step 1 dispatch)
## CHG-0033 - Observability & Forensic Trace Audit (audit-only, no code) (2026-08-31 Hermes-ObsForensic)

Change: AUDIT-ONLY deliverable - docs/architecture/observability-map.md + artifacts/forensics/observability-audit.json (OBS-001..016 gap ledger, reconstruction scorecard, 10-scenario black-box reconstruction results). NO runtime code, trading, model, release, or config changes; zero-behavior-impact by construction (two additive docs + registry rows).
Scope: docs/architecture/observability-map.md (new), artifacts/forensics/observability-audit.json (new), agents/taskboard.md (TASK-OBS-AUDIT row), agents/change_control.md (this entry).
Why: user-mandated forensic observability audit - determine whether a developer can reconstruct incidents from runtime evidence alone; evidence-based gap ledger for future fixes (P0: redaction eats EXEC- ids, correlation ids never bound; P1: dead /api/debug/trace audit_db import, silent DB batch drops, EXPERIENCE execution_id 0%%, update chain json-only).
Migration: none - no schema/contract/feature change; OBSERVABILITY_AUDIT v1 is a documentation artifact.
Verification: black-box reconstruction of 10 scenarios from runtime evidence (logs 190MB severity-split, 9 DBs, incidents, update-state.json, live API :8081 probes incl. X-Request-ID echo + dead debug/trace endpoint, real CLI execution); 6 parallel read-only code sweeps for every claim; no runtime claims made.
Risk: LOW - additive docs only. Recommended fixes are listed for future authorized tasks, NOT implemented (audit-only mandate).
Status: VERIFIED (audit artifacts complete; no runtime verification applicable)
## CHG-0034 - Provider rate-limit hardening: global provider gate, auto-disable, Strategy Factory toggle (2026-09-01 Nexus-Main)

Change: HARDENING of the optional external-provider subsystem (Strategy Factory LLM + News AI) after repeated HTTP 429 from the configured provider. New module src/nexus_scalp/strategies/factory/provider_gate.py = ONE global provider gate (config validation, bounded concurrency, token-bucket rate limit, bounded retries with Retry-After + exponential backoff + jitter, circuit breaker AVAILABLE/RATE_LIMITED/CIRCUIT_OPEN/COOLDOWN/HALF_OPEN/RECOVERED, single-flight dedup, bounded-queue staleness, secret-free health snapshot). LLMGenerationProvider routes ALL requests through the gate; httpx transport retries=0 (one retry owner). Settings DB keys factory.enabled / factory.auto_disabled / factory.auto_disabled_reason = user intent vs runtime health (effective_enabled). New web endpoints GET /api/factory/provider-health, POST /api/factory/provider-toggle, POST /api/factory/provider-test (one controlled probe). Web UI: Strategy Factory ENABLED/DISABLED toggle + auto-disable reason panel. Auto-disable ONLY the provider feature - trading engine untouched.
Scope: src/nexus_scalp/strategies/factory/provider_gate.py (new), provider.py, settings/service.py, web/factory_routes.py, news/ai_service.py, application/live_engine.py (additive guard in _build_factory_llm_provider ONLY - deferred-file disclosure per CHG-0032-A1; not decomposition), Web/index.html, Web/app.js, tests/unit/test_provider_gate_hardening.py (new), agents/* registries.
Why: user MASTER STEER 2026-09-01 (provider rate-limit hardening + API/host health gate + Strategy Factory toggle). Evidence: provider.py had NO 429/backoff/circuit handling; pro_auto.py soft-retry + news worker requeue x3 = retry amplification; no user toggle existed. BUG-186 (429 storm/retry amplification), BUG-187 (no toggle/auto-disable).
Contracts touched: PROVIDER_HEALTH_GATE v1 (new), PROVIDER_USAGE v1 (extended fields), SETTINGS_DB factory.* keys v2. NOT touched: 70D/scalp_v3/model artifacts, feature schema, execution, risk, research paths, thresholds (explicit no-change per steer sections 53-57).
Owners affected: Hermes-News (pro_auto fallback unchanged in behavior), Hermes-UI (factory tab additive), Hermes-Runtime (live_engine additive guard only).
Risk: MEDIUM (provider subsystem only; isolation tests mandatory - provider failure must never block the trading loop; INV-024 registered).
Required tests: config validation matrix, 429 Retry-After/bounded retry, 401/403 auth-fail, circuit open/half-open/recover, rate-limit bound, concurrency cap, single-flight, queue staleness, user toggle, secret-redaction, trading isolation.
Status: IMPLEMENTING
## CHG-0035 - Research execution stack closure: TRUE FORWARD TEST + TICK REPLAY + provenance hardening (2026-09-01, Hermes-Main)

Change: closes Agent-3's remaining research-path gaps WITHOUT touching any passing path (backtest/walk-forward/replay-70D/MT5 probes). (1) research/forward_test.py = dedicated ForwardTestExperiment experiment_type=FORWARD_TEST: freeze capture at cutoff (model fingerprint = sha256 of live artifact bytes, scaler fingerprint = sha256 of model.scaler.npz, strategy fingerprint = sha256 of frozen SignalPolicy constructor params, schema hash = features.schema_contract.feature_schema_hash, execution config, git commit via release.metadata), hard causal cutoff boundary; frozen artifacts are snapshotted to artifacts/forward_test/<run_id>/ (bytes, not symlinks) so later champion changes cannot mutate the experiment; runner streams ONLY ticks/bars with timestamp > cutoff through the SHARED streaming replay engine; freeze invariants re-verified after the run. (2) research/event_source.py = HistoricalEventSource abstraction: BarEventSource (rates frame), TickEventSource (raw tick frame: time_msc/bid/ask/last/flags/volume_real preserved), ChunkedEventSource (bounded tick chunks; boundaries must not change semantics - enforced by test), validate_event_source (ordering/duplicates/bid-ask sanity/finiteness/gap detection); DATA_ERROR records are returned for malformed ticks (never fabricated). (3) research/streaming_replay.py = ONE StreamingReplayEngine shared by bar + tick + forward-test modes: logical clock (event timestamps only, zero wall-clock sleeps), incremental BarAggregator from ticks, causal feature snapshot (ScalpFeatureEngine 50D window + news_context_at + liquidity engine at decision T), local ScalpNet 70D bundle inference (torch.inference_mode, loaded ONCE per session), FROZEN SignalPolicy.evaluate_probabilities, RiskEngine.evaluate_proposal, direction-aware simulated execution (BUY entry ASK / SELL entry BID / BUY exit BID / SELL exit ASK; historical bid/ask only), tick-chronological SL/TP first-touch resolution, logical latency fields (signal/decision/order/fill timestamps), TradeLedger summary (MFE/MAE from replayed path). NO adapter import, NO mt5.order_send anywhere in the replay path (INV; test-enforced), NO retraining, NO parameter mutation. (4) research/mt5_tick_dataset.py = tick dataset acquisition+cache using the ALREADY-PROBED adapter surface (DirectMT5Adapter.get_tick_history -> copy_ticks_range COPY_TICKS_ALL, fields verified by Agent-3's probe suite test_mt5_api_probes.py); chunked bounded RAM; local parquet/npz cache keyed by (symbol, range, fingerprint); after acquisition the engine runs 100% offline (MT5 closed) - test-enforced. (5) PROVENANCE: audit_repository research_run_snapshots gains feature_schema_id/feature_dimension/model_id/git_commit columns (ADD COLUMN guarded, idempotent); ResearchRunSnapshot model + build_run_snapshot capture them from the LIVE schema contract + artifact bytes + git commit (empty string = NOT RECORDED, never invented); pipeline.py validate_candidate passes the resolved identity through configuration; backfill NOT performed for existing rows (no authoritative evidence at this time) - recorded honestly as NOT_RECORDED.
Scope: src/nexus_scalp/research/{forward_test.py,event_source.py,streaming_replay.py,mt5_tick_dataset.py} (new), src/nexus_scalp/research/evidence.py (snapshot fields), src/nexus_scalp/research/observability.py (snapshot SQL columns), src/nexus_scalp/adapters/database/audit_repository.py (additive DDL columns), src/nexus_scalp/research/pipeline.py (identity capture at snapshot build), tests/integration/test_research_execution_stack.py (new), tests/unit/test_forward_test_freeze.py (new), tests/unit/test_research_provenance_immutable.py (new), agents/* registries.
Why: Agent-3 final verdict - FORWARD TEST = NO DEDICATED MODE (shadow70 is a runtime disagreement observer, NOT a frozen-experiment runner: no cutoff boundary, no future-data isolation, no experiment result identity), TICK REPLAY = architectural gap (REPLAY-70D recomputes vectors on bar frames; no tick event stream through strategy+risk+execution), research_run_snapshots model_version/feature_schema_version recorded empty (P2 gap). User research-completion brief 2026-09-01 sections 4-11, 12-23, 33-39.
Contracts touched: FORWARD_TEST_EXPERIMENT v1 (new), HISTORICAL_EVENT_SOURCE v1 (new), STREAMING_REPLAY v1 (new), MT5_TICK_DATASET v1 (new), RESEARCH_RUN_SNAPSHOT v2 (4 new columns). NOT touched: LiveEngine hot path, order_manager, execution adapters, policy.py (CHG-0032-A1 deferred files untouched), 70D schema/scaler/model artifacts, walk-forward implementation, MT5 probe suite.
Owners affected: Nexus-QA (new test surface), Agent-3 (research validation re-run), devops (no runtime/CI change beyond test files).
Risk: MEDIUM (research-only subsystem; zero live-trading surface; replay never sends orders - invariant test-enforced).
Required tests: freeze identity captured+re-verified post-run, future-data poison (pre-cutoff state unchanged), replay determinism (two runs byte-identical ledger), chunk determinism (1 chunk == N chunks), resume determinism, no order_send in replay, direction-aware pricing, tick SL/TP first-touch, bar/tick difference classification, offline-after-acquisition, model-offline inference, provenance immutability across champion change, empty/future-range honest outcomes.
Status: IMPLEMENTING

## CHG-0036 - LIVE MT5 tick acquisition certification + BUG-188 boundary fix (2026-09-01, Hermes-Main)

Change: closes the CHG-0035 explicitly-unverified residual (acquire_ticks
against a REAL terminal) via a bounded read-only live probe, then the
smallest safe boundary fix for the discovered BUG-188 double-conversion
(input window +180min in get_tick_history, symmetric with broker_epoch_to_utc
output conversion), plus targeted offline regression tests
(tests/unit/test_mt5_tick_boundary_bug188.py) and a sanitized certification
evidence artifact (artifacts/forensics/mt5_tick_certification.json - no
market-data dumps, no credentials).
Scope: src/nexus_scalp/adapters/mt5/mt5_adapter.py (get_tick_history input
boundary ONLY), tests/unit/test_mt5_tick_boundary_bug188.py (new),
artifacts/forensics/mt5_tick_certification.json (new, sanitized), agents/*
registries. NOT touched: research engine semantics (event_source/
streaming_replay/forward_test), provenance schema, walk-forward, backtest,
strategy core, risk engine, provider gate, installer, UI.
Why: user certification brief 2026-09-01 (live probe A-F checks; live/stub
parity; smallest boundary fix; no scope creep).
Contracts touched: MT5_TICK_HISTORY_INPUT_TIMEBASE v2 (input boundary now
broker-timebase-shifted, symmetric with output conversion). NOT touched:
MT5_TICK_DATASET v1 cache identity/fingerprint semantics, HistoricalEventSource.
Risk: LOW (single function input-boundary normalization; offline-tested;
parity with the already-proven history-deal window convention).
Status: COMPLETE

## CHG-0039 - Provider gate lifecycle hardening: state ownership, credential rotation recovery, restart semantics (2026-09-01 Nexus-Main)

Change: POST-CERTIFICATION lifecycle layer on top of live-certified CHG-0034 (rate-limit/circuit implementation NOT reopened). Forensic pass found and fixed: DEFECT-1 (live-confirmed): settings-layer auto_disabled had NO production writer, so the health payload/UI showed ENABLED while the gate was AUTO_DISABLED. Fix: the gate is the RUNTIME authority; factory_health_snapshot(runtime_override=...) merges gate truth into the authoritative top-level fields (provider-health endpoint). DEFECT-2: transient auto-disable must never be persisted (sticky across key rotation forbidden) - settings DB keeps USER INTENT only; record_/clear_ methods remain for API compatibility. DEFECT-3: llm-config save in web-only mode (no in-process factory) never reconfigured the gate - now the process singleton is reconfigured on every save. DEFECT-4: provider-test while AUTO_DISABLED could not verify a rotated key - the probe path now reconfigures the singleton first (still EXACTLY ONE network request).
Scope: settings/service.py (snapshot runtime_override), web/factory_routes.py (health merge + web-only reconfigure + probe reconfigure), tests/unit/test_provider_lifecycle_hardening.py (new, 18 tests), agents/*.
Why: user steer 2026-09-01 (post-certification): deterministic recovery lifecycle, state consistency, restart semantics, operator UX truthfulness.
Contracts: PROVIDER_HEALTH_GATE v1 extended (runtime authority semantics); SETTINGS_DB factory.* v2 semantics clarified (intent persisted, runtime transient). BUG-189 (UI/backend state contradiction) filed.
Risk: LOW-MEDIUM (state reporting + recovery paths; no trading, no 70D, no rate-limit/circuit behavior changes).
Status: IMPLEMENTING
## CHG-0038 - Data-to-decision fidelity audit: tick->bar->70D->inference->policy->ledger (2026-09-01, Hermes-Main)

Change: full fidelity audit from certified live ticks to simulated outcome, with a
machine-readable tensor audit, deterministic causality probes, inference-parity
measurement, policy parity, NO_TRADE first-gate attribution, DB reconciliation and a
bounded counterfactual; one genuine defect found (BUG-190 live news-block key mismatch)
with a minimal boundary fix + RED->GREEN regression test
(tests/unit/test_fidelity_data_to_decision.py, 13 tests).
Scope: src/nexus_scalp/application/live_engine.py (news projection call sites ONLY:
_build_live_feature_vector + _build_retrain_record), tests/unit/test_fidelity_data_to_decision.py,
artifacts/forensics/fidelity_audit_20260901.json, agents/* registries.
NOT touched: policy.py, risk engine, regime classifier, event_source/streaming_replay/
forward_test (CHG-0035 certified paths), provenance schema, walk-forward, backtest,
provider gate, installer, observability (Agent-2), CLI/docs owners.
Why: user fidelity-audit brief 2026-09-01 (find the FIRST divergence between real ticks
and the engine's interpretation; fix only evidence-backed defects).
Contracts touched: LIVE_NEWS_10D_PROJECTION v2 (engine now uses the canonical named
projection). Risk: LOW (mapping parity fix; bit-identical outputs on the current
smoke-grade artifact verified pre/post).
Status: COMPLETE

## CHG-0041 - Research tick store + NO_TRADE counterfactual engine (2026-09-01, Hermes-Main)

Change: closes the CHG-0038 counterfactual small-N gap. (1) mt5_tick_dataset.py
extended: TICK_DATASET_META v2 provenance (source, acquisition_time, adapter
surface, git_commit, requested window, fingerprint), overlap dedup (only
missing sub-windows fetched, merged chronologically), immutable fingerprinted
parquet. (2) research/counterfactual.py = TICK_COUNTERFACTUAL v1: joins
audit_signals NO_TRADE rows (decision id, timestamp, action=NO_TRADE,
confidence, entry/SL/TP geometry, regime, gate, model probs) with the tick
store; hypothetical entry at T via the CERTIFIED direction-aware semantics
(BUY@ASK/SELL@BID); tick-walk to SL/TP/horizon: MFE/MAE (favorable/adverse
price excursion), future return, cost = entry spread, time-to-target,
time-to-stop, RR_NOT_RECORDED when geometry absent; classification
FALSE_REJECTION / CORRECT_REJECTION / MISSED_LOSER / INCONCLUSIVE with
evidence-based rules (R>=+0.5R = rejected winner, R<=-0.5R = would-be loser,
insufficient coverage = INCONCLUSIVE); stratification by gate/regime/
confidence band/session/direction; fingerprinted deterministic outputs.
(3) tests/unit/test_counterfactual_engine.py (offline, synthetic deterministic
ticks + decision fixtures). (4) evidence artifact
artifacts/forensics/no_trade_counterfactual_20260901.json.
Scope: src/nexus_scalp/research/{counterfactual.py,mt5_tick_dataset.py},
tests/unit/test_counterfactual_engine.py, artifacts/forensics/*, agents/*.
NOT touched: policy, risk, regime engine, execution adapters, certified
replay/forward-test paths, observability, installer, CLI owners' WIP.
Why: user counterfactual-engine brief 2026-09-01; prior sample N=17 INCONCLUSIVE.
Contracts touched: TICK_DATASET_META v2, TICK_COUNTERFACTUAL v1. Risk: LOW
(research-only, no live path).
Status: COMPLETE


## CHG-0042 - Confidence-semantics repair: policy gate measures trained-class directional share (2026-09-02, Hermes-Main)

Change: SignalPolicy now normalizes the candidate's OWN-side directional
probability over the TRAINED classes (BUY+SELL+NO_TRADE) before comparing it
with the existing thresholds (0.40 base / 0.50 range - UNCHANGED). The
4-logit head's WAIT slice (never a training label: TripleBarrierLabeler
3-class + LABEL_SCHEMA_3CLASS_V1; online fine-tune class_counts [.., 0]) no
longer dilutes directional confidence. Degenerate vectors (zero/non-finite
mass, malformed width) fall back to the pre-fix raw semantics instead of
manufacturing confidence; prob_no_trade is sanitized like its siblings
(NaN-slice poisoning found by the new regression net). confidence_source
(DIRECTIONAL_NORMALIZED | RAW_FALLBACK) is stamped into every proposal's
risk_checks for explainability.
Evidence: NO_TRADE forensic e1f95e5 (0/464 candidates pass; all-time max raw
probability 0.357 < 0.40); counterfactual artifacts/forensics/
confidence_repair_counterfactual_20260902.json (BEFORE 0 passers, AFTER 13
on the same recorded set, 0 in RANGING - consistent with the ~32 all-data
projection; no threshold tuning).
Scope: src/nexus_scalp/signals/policy.py,
tests/unit/test_confidence_semantics_repair.py (13),
tests/unit/test_policy.py (telemetry assertions to the repaired measure).
NOT touched: regime_classifier, order_manager, live_engine, risk engine,
70D contract/artifacts, provider gate, installer, observability contract.
Why: user confidence-pipeline-repair brief 2026-09-02. Contracts touched:
CONFIDENCE_SEMANTICS v2 (risk_checks +confidence_source). Risk: MEDIUM
(hot-path decision semantics; mitigated by fallback + full gate battery).
Status: COMPLETE

## CHG-0043 - Replay-on-Chart: historical session controller + no-future-data certification (2026-09-02, Hermes-Main)

Change: user replay-on-chart brief. (1) SURGICAL behavior-preserving refactor of
research/streaming_replay.py: extract per-event processing + run-finalization so
ONE decision path serves both run() and a new stepwise controller (no second
engine, no second execution model). (2) research/replay_session.py =
REPLAY_SESSION v1: ReplayContract (replay_id/dataset_id/window/model identity/
schema/policy/risk identity/git_commit/replay_mode=BAR_REPLAY), authoritative
ReplayClock (event time only), step/play/pause/reset/seek/checkpoint with
equivalence tests, bounded decision-trace ring (per-decision: timestamp, price,
probs, confidence, action, regime, gates, first blocking gate, candidate
geometry), causal regime wiring (MarketRegimeClassifier on replay ticks,
guarded by regime_enabled flag, default preserves current behavior),
checkpoints every N bars (full state: bars window, policy state, regime rings,
equity, position, counters). (3) web/replay_routes.py: POST /api/replay/session,
POST /api/replay/control, GET /api/replay/state, GET /api/replay/report —
serves persisted truth (research_run_snapshots v2 + evidence JSON). (4) Web/
replay panel: KNOWN-vs-UNKNOWN chart semantics, decision drill-down, NO_TRADE
reasons. (5) Tests: future-mutation invariance A-F (price/news/liquidity/
volume/regime-labels/db-rows), seek==sequential, checkpoint equivalence, 70D
mapping, model fingerprint, E2E DB reconciliation, clock-contract (no
datetime.now in decision path), END_OF_DATA, step determinism.
Scope: src/nexus_scalp/research/{streaming_replay.py,replay_session.py(new)},
web/replay_routes.py(new), Web/{index.html,app.js}, tests/unit/test_replay_session_*.py,
tests/integration/test_replay_e2e_reconciliation.py, artifacts/forensics/*,
agents/*. NOT touched: signals/policy.py, features/regime_classifier.py,
execution/order_manager.py (foreign Agent-5 WIP), application/live_engine.py,
adapters/mt5, provider_gate, installer WIP, settings.
Why: brief 2026-09-02 — chart must be the operator surface of the REAL
historical decision pipeline with ZERO future-data leakage.
Contracts touched: STREAMING_REPLAY v1 (refactor, hash-equivalence proven),
REPLAY_SESSION v1 (new), REPLAY_API v1 (new), RESEARCH_RUN_SNAPSHOT v2 (reused).
Risk: MEDIUM (touches certified engine file; mitigated by pre/post hash
equivalence on real data + full research-family suite green).
Status: IMPLEMENTING

## CHG-0043 — Runtime Truth / Release Awareness / Feature-Model-DB Consistency Hardening
Agent: Nexus-Main (orchestrator; implementation via nexus-coder)
Role: Runtime identity / health truthfulness / release visibility
Task: TASK-RUNTIME-TRUTH (user brief 2026-09-02)
Scope: release/ state taxonomy + canonical runtime snapshot; metadata commit_source + dev stale build-info precedence; health.py truthful states (NOT_CONFIGURED/NOT_INITIALIZED/DISABLED/INFO vocabulary, MODEL_CONTRACT wired into run_all, FEATURE_SCHEMA resolved from configured artifact, NEWS table names fixed to real schema, update-state check offline-safe); cli/doctor.py render semantics (NOT_RECORDED, INFO); web live/state features block uses effective contract; NEW /api/release/status (no network); Web UI identity strip (surgical); tests.
Affected files: src/nexus_scalp/release/{state_taxonomy.py(new),runtime_snapshot.py(new),metadata.py,health.py,versioning.py,updater.py(additive fields)}, src/nexus_scalp/cli/doctor.py, src/nexus_scalp/web/{diagnostics_state_routes.py,release_routes.py(new)}, Web/{index.html,app.js}(minimal), tests/unit/*, agents/*
Contracts touched: RUNTIME_IDENTITY v2 (commit_source/commit_status), HEALTH_ENTRY v2 (INFO verdict + canonical states), RELEASE_STATUS_API v1 (new), LIVE_UI_STATE v2 additive (features.schema_id truthfulness + feature_activation additive); 70D contract UNCHANGED (features/schema_contract.py read-only); features/schema.py ACTIVE_SCHEMA_ID constant left as legacy residue per nse-50d ruling (runtime truth reads the artifact, not the constant).
Owners affected: Hermes-Release (release/), Hermes-UI (Web/) — CROSS-OWNER CHANGE declared; disclosed UI collision surface with TASK-REPLAY-ON-CHART (edits confined to identity/health render sites, replay files untouched).
Runtime paths touched: CLI doctor/version, /api/status|/health|/api/live/state (additive), NEW /api/release/status. NO hot-path change (snapshot cached 60s where wired; no new DB writes; no network in health).
Forbidden: installer/*, provider_gate, order_manager, live_engine edits, regime engine, observability SSOT, research/*, training pipeline, model training.
Risk: MEDIUM (health semantics change: optional-subsystem WARNs become INFO with canonical state; READY aggregate can only get MORE truthful — MODEL_CONTRACT FAIL on genuine width mismatch now blocks, which is the intended truth).
Required tests: new unit suites (taxonomy, snapshot identity, doctor semantics, release status endpoint) + affected existing suites + beforePush CRITICAL gate.
Status: IMPLEMENTING


## CHG-0044 - OSS-grade adversarial QA / deep-assurance layer (2026-09-02, Hermes-Main)

Change: independent QA-adversarial brief. (1) tests/unit/test_qa_deep_*.py families:
tensor-contract adversarial + property (swapped/NaN/inf/extreme/dtype/bool/str/None, 
metamorphic news/liquidity neutrality, replay determinism), state-machine walkers with
seeded bounded generators (ProviderGate, PositionStateMachine, RecoveryBudgetLedger) +
single-flight concurrency race with barriers, DB migration adversarial (idempotency,
partial failure rollback, lock contention, tamper, downgrade, baseline, orphan rows on
disposable DBs), API contract adversarial via TestClient (schema/error envelope/JSON
purity/idempotency/malformed/oversized/auth boundary), security surface (redaction
metamorphics, secret leakage, path traversal, oversized inputs, deserialization probe),
observability chaos (EventBatchAggregator invariants, storm bounds, deterministic fault
injection harness with failure classification), execution safety (order_send-never-called
hard assertion on research/replay paths, duplicate-order idempotency, side/volume/SL/TP
preservation via fake port). (2) scripts/qa/deep_assurance.py orchestrator: --fast,
default, --json (valid JSON only, suite_version/git_commit/seed/durations/defects[]),
--mutation (runtime-behavioral mutations at contract seams; source files NEVER rewritten),
--offline default. (3) docs/architecture/QA_BLIND_SPOT_MATRIX.md + qa-assurance-contract.md.
(4) pyproject markers additive: qa_deep, adversarial, property, security.
(5) BUG-192: validate_70d_vector bool-accepted / str-None TypeError-crash; same class in
InferenceValidator.validate. Minimal type-guard fix + regression net; existing 70D parity
battery re-run to prove zero behavior change on valid vectors.
Scope: tests/unit/test_qa_deep_*.py, scripts/qa/deep_assurance.py, docs/architecture/*,
agents/*, pyproject.toml (markers), .github/workflows/qa-deep-assurance.yml (NEW file),
features/schema_contract.py + features/inference_validator.py (BUG-192 minimal type
guards ONLY - CROSS-OWNER CHANGE to 70D contract SSOT, disclosed to contract owner).
NOT touched: live_engine, order_manager, policy, risk, provider gate logic, installer,
migrations content, observability implementations, ci.yml, release workflows.
Why: user OSS-Grade adversarial QA brief 2026-09-02. Contracts touched: none new
(guards FEATURE_SCHEMA_70D, INFERENCE_CONTRACT, PROVIDER_HEALTH_GATE v1, DB_MIGRATION v1,
OBSERVABILITY_LOG_CONTRACT). Risk: LOW (test-only surface + two-line type guards on
malformed-input paths; valid-input behavior byte-identical).
Status: IMPLEMENTING

CHANGE-ID: CHG-0043-P2 (addendum, part 2 scope detail)
Agent: Hermes-Main
Task: TASK-REPLAY-ON-CHART
Scope additions (2026-09-02):
  - research/replay_session.py: ReplaySession controller (REPLAY_SESSION v1):
    ReplayContract identity (fingerprinted, deterministic replay_id),
    authoritative event-clock, play/pause/step_tick/step_bar/reset/seek,
    checkpoints every N bars (full state snapshot), bounded decision-trace
    ring with per-decision evidence (probs, confidence, action, regime,
    gates, candidate geometry), regime_enabled flag (default False preserves
    legacy byte-parity; True wires the PRODUCTION MarketRegimeClassifier fed
    ONLY by replay events - regime state then flows to FrozenPolicyRunner
    (guardian gate live) and RiskEngine (replay regime state)).
  - web/replay_routes.py: REPLAY_API v1 (session/control/state/report).
  - Web/: replay panel (KNOWN/UNKNOWN chart semantics, decision drill-down).
  - tests: mutation-invariance A-F, seek/checkpoint equivalence, clock
    contract, E2E reconciliation.
NOT touched: policy/regime/execution/live_engine internals (identity and
  semantics consumed as-is per brief section 10: replay reveals truth, does
  not modify it).
Status: IMPLEMENTING


## CHG-0043 - Versioned read-only API platform /api/v1 (TASK-API-PLATFORM) (2026-09-02, Hermes-Main)

Change: NEW subsystem src/nexus_scalp/api/v1/* exposing ~40 real, read-only API
capabilities over EXISTING backends (audit repository, incident store, research
store/registry, shadow stores, feature schema contract, release metadata, config
pydantic models, live engine observability attributes) mounted at /api/v1 via a
single include_router in web/server.py create_app. Standardized error envelope
(reuses web/errors.py safe_error_payload contract + X-Request-ID), one pagination
model (page/page_size, bounded), UTC ISO-8601 timestamps, capabilities discovery,
request-id continuation. Mutation surface deliberately ZERO (no trading actions).
Developer tooling: nexus api CLI group (consumes the same HTTP contracts via
TestClient-embedded mode), scripts/dev/api_smoke.py, api_contract_check.py,
api_snapshot.py, api_diff.py. Tests: contract suite (happy/validation/empty/
not-found/dependency-unavailable/pagination/filters), bounded property-style
pagination tests, security checks (no secret keys, no stack traces), OpenAPI
quality gate. docs/API_PLATFORM.md developer reference.
Why: user API-platform brief 2026-09-02 (40+ real capabilities; no duplicates of
existing 257 routes; truthful data only - NO fake fallbacks).
Scope: NEW files only + 1-line include_router in web/server.py + pyproject/CI
additive rows. NOT touched: strategy, risk policy, execution internals, order
manager, live_engine hot path, model artifacts, 70D contract, installer, settings.
Contracts touched: API_PLATFORM v1 (new). Risk: LOW (read-only, additive, new
prefix; existing 257 routes and UI untouched).
Status: IN_PROGRESS

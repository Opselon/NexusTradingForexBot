# CHANGE CONTROL REGISTRY — Nexus Scalp Engine (NSE)

> Maintained per the MASTER MULTI-AGENT CONTRACT §4 (see `agents/multi-agent-git-contract.md`).
> Register a change entry BEFORE a meaningful architectural or shared-code change.
> Status lifecycle: PROPOSED → IMPLEMENTING → VERIFIED → READY_FOR_REVIEW → MERGED | REJECTED.

## Open / recent changes
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
```

## Notes
- Substantial tasks also get a TASK-ID in `agents/taskboard.md` and a handoff in `docs/agent_handoffs/`.
- Reference CHANGE-ID in PRs/issues alongside BUG-NNN / TASK-ID (contract §42).

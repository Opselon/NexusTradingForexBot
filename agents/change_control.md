# CHANGE CONTROL REGISTRY — Nexus Scalp Engine (NSE)

> Maintained per the MASTER MULTI-AGENT CONTRACT §4 (see `agents/multi-agent-git-contract.md`).
> Register a change entry BEFORE a meaningful architectural or shared-code change.
> Status lifecycle: PROPOSED → IMPLEMENTING → VERIFIED → READY_FOR_REVIEW → MERGED | REJECTED.

## Open / recent changes

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
| CHG-0001 | Hermes-Behavior | TASK-2 | Behavioral/anomaly intelligence: wire BehaviorDetectionEngine into IntelligenceWorker; add evidence-based detectors; versioned idempotent persistence; report truth-states (NO_DATA/CLEAR/FLAGS_FOUND/ANOMALIES_FOUND) + coverage | BEHAVIOR_ANALYSIS v1 (new), ANOMALY v1 (new) | Hermes-Accounting (reporting), Hermes-TradeLifecycle (TASK-3) | MEDIUM | IMPLEMENTING |
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
Status: IMPLEMENTING
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

# TASKBOARD — Nexus Scalp Engine (NSE)

> Maintained per the MASTER MULTI-AGENT CONTRACT §5 (see `agents/multi-agent-git-contract.md`).
> Goal: prevent duplicated work; prevent two agents from silently solving
> the same problem differently.
> Statuses: TODO, IN_PROGRESS, BLOCKED, WAITING_FOR_AGENT, READY_FOR_REVIEW, VERIFIED, MERGED, REJECTED.

## Tasks

| TASK-ID | Owner | Priority | Title | Deps | Files | Contracts | Blocker | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| TASK-2 | Hermes-Behavior | HIGH | Behavioral Intelligence / Anomaly Detection Forensic Repair (eliminate n/a) | BUG-081/082, TASK-1, TASK-3 | intelligence/behavior.py, intelligence/worker.py, reporting/, adapters/database/audit_repository.py, web/server.py | BEHAVIOR_ANALYSIS v1, ANOMALY v1 | none | IN_PROGRESS |
| TASK-3 | Hermes-TradeLifecycle | HIGH | Canonical Trade Lifecycle / Exit Intelligence / Learning-Lineage Forensic Repair | TASK-1, TASK-2, BUG-081/082 | order_manager, outcome_recovery, intelligence/, experience/, accounting/normalize, features/schema | TRADE_EXECUTION_CONTEXT v2, TRADE_OUTCOME v3, EXIT_CLASSIFICATION v2 | none | IN_PROGRESS |
| TASK-5 | Hermes-ModelIntelligence | HIGH | Adaptive Model Intelligence / 60D Challenger / Continuous Learning Forensics | TASK-1..4, BUG-082, INV-009 | features/schema_augment.py, model_generation/schema_v2.py, features/schema.py, model_generation/{training,validation,benchmark,__init__}.py, model_lifecycle/worker.py | MODEL_MANIFEST v1, FEATURE_SCHEMA v1 (scalp_v2 60D), TRADE_OUTCOME v3 | none | READY_FOR_REVIEW |
| TASK-6 | Hermes-ModelGovernance | HIGH | Live Model Governance / Shadow Runtime / Champion-Challenger Integration | TASK-1..5, BUG-025..029 | governance/, shadow/, model_lifecycle/, model_generation/, live_engine, web, reporting | MODEL_GOVERNANCE v1, MODEL_LOAD_GATE v1, SHADOW_PARITY v1, PROMOTION_STATE_MACHINE v1 | none | IN_PROGRESS |
| TASK-4 | Hermes-Research | HIGH | Strategy Research / Discovery / Validation Data-Integrity Forensic Repair | TASK-1, TASK-2, TASK-3, BUG-045/073/081 | research/ (dataset, discovery, pipeline, worker, scoring, store, registry), experience/, web/server.py, Web/ | RESEARCH_RESULT v1, TRADE_OUTCOME v3, FEATURE_VECTOR_50D | none | IN_PROGRESS |
| TASK-7 | Hermes-PositionMgmt | HIGH | Exit Intelligence / Position Management / Adaptive Risk Protection Repair | TASK-1..TASK-6, BUG-054/055/056/067/072/073/074/081/082 | execution/order_manager.py, application/live_engine.py, intelligence/lifecycle.py, adapters/database/audit_repository.py, agents/bugs.md | EXIT_CLASSIFICATION v2, POSITION_STATE (TASK-7), HOLD_SCORE (TASK-7) | none | IN_PROGRESS |
| TASK-1 | Hermes-TASK1 (Performance Intelligence Data-Truth Auditor) | CRITICAL | Performance Intelligence Data-Truth audit + repair: canonical trade graph, metric truth (PF/expectancy/R/MAE/MFE/drawdown/funnel), PnL & count reconciliation, exit classification, report truthfulness | BUG-081 (baseline) | accounting/, reporting/, execution/order_manager.py (read-only exit), experience/outcome_recovery.py (read-only), adapters/database/ | TRADE_OUTCOME v3, EXIT_CLASSIFICATION v2, ACCOUNTING_SNAPSHOT v1 | none | IN_PROGRESS |

## Notes
- Any substantial work: claim a TASK-ID here BEFORE starting, so parallel agents can see it.
- In-progress/blocked items should reference the owning agent (Agent: name) and CHANGE-ID from `agents/change_control.md`.
- TASK-7 owns: exit-decision traceability, protective-SL monotonic invariant, broker-verified close ordering, exit priority, BE/trailing audit, hold/protection score forensics, historical exit forensics. Entry strategy untouched; no risk relaxations.
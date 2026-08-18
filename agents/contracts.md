# CONTRACT REGISTRY — Nexus Scalp Engine (NSE)

> Maintained per the MASTER MULTI-AGENT CONTRACT §7 (see `agents/multi-agent-git-contract.md`).
> This file is the authoritative index of cross-subsystem contracts.
> Source of truth for behavior is the executable code (agents/skill.md §0 rule).
> **Additive only** — never silently rewrite established contract entries.

## Contract index

| Contract | Version | Status | Producer | Consumers |
| :--- | :--- | :--- | :--- | :--- |
| FEATURE_VECTOR_50D | v1 (schema-controlled) | ACTIVE | features/scalp_features.py | models, training, experience |
| FEATURE_SCHEMA | v1 | ACTIVE | features/ | models, research, replay |
| TRADE_EXECUTION_CONTEXT | v2 (parent-child lineage) | ACTIVE | execution/order_manager.py | experience, accounting |
| TRADE_OUTCOME | v3 | ACTIVE | experience/outcome_recovery.py | accounting, research |
| ACCOUNT_SNAPSHOT | v1 | ACTIVE | accounting/ | web, telegram |
| MT5_BROKER_SNAPSHOT | v1 | ACTIVE | adapters/mt5/ | live_engine, accounting |
| NEWS_CONTEXT | v1 | ACTIVE | news/ | live_engine, signals |
| STRATEGY_CANDIDATE | v1 (content-addressed) | ACTIVE | strategies/ | research worker |
| MODEL_GOVERNANCE | v1 | ACTIVE | governance/ | live_engine, web, telegram (TASK-6/CHG-0003) |
| MODEL_LOAD_GATE | v1 | ACTIVE | governance/load_gate.py | shadow attach, registry (TASK-6) |
| SHADOW_PARITY | v1 | ACTIVE | governance/alignment.py | shadow runtime (TASK-6) |
| PROMOTION_STATE_MACHINE | v1 | ACTIVE | governance/engine.py | web, telegram (TASK-6) |
| MODEL_MANIFEST | v1 | ACTIVE | model_lifecycle/ | training, runtime |
| FEATURE_SCHEMA_60D | v1 (scalp_v2, candidate-only) | ACTIVE | features/schema_augment.py, model_generation/schema_v2.py | model_generation training/benchmark, TASK-6 governance |
| LIQUIDITY_60D | v1 (scalp_liquidity_v1, candidate-only) | ACTIVE | features/liquidity_engine.py, schema.py, model_generation/schema_v2.py | 70D series (TASK-02..07), model_generation, research (TASK-01-60D-LIQUIDITY) |
| FEATURE_SCHEMA_70D | v1 (scalp_v4, candidate-only) | ACTIVE | features/schema.py (registered by TASK-02-70D-INTEGRATION; consumed by TASK-04 benchmark protocol) | model_generation training/benchmark, TASK-4+ governance/validation |
| ACCOUNTING_SNAPSHOT | v1 | ACTIVE | accounting/ | web, telegram |
| EXIT_CLASSIFICATION | v3 (evidence provenance) | ACTIVE | experience/outcome_recovery.py | ledger, accounting, telegram |
| RESEARCH_RESULT | v1 | ACTIVE | research/ | registry, web |
| UPDATE_SYSTEM | v1 | ACTIVE | release/updater.py, cli/main.py | installed users, release CI |
| SHADOW_70D | v1 | ACTIVE | shadow/shadow70/ (TASK-05-70D-SHADOW) | web, research, drift | 
| SHADOW_LOAD_GATE | v1 | ACTIVE | shadow/shadow70/runtime.py (manifest/hash/schema/dimension/scaler) | shadow70 loader |
| SHADOW_FEATURE_HEALTH | v1 | ACTIVE | shadow/shadow70/health.py | web, drift monitor |
| SHADOW_DRIFT | v1 | ACTIVE | shadow/shadow70/drift.py | web, alerting |
| UI_STATE | v1 (900-bar standard) | ACTIVE | web/ | dashboard |
| DATABASE_HYGIENE | v1 | ACTIVE | hygiene/worker_runner.py | CLI, API, live_engine |
| RETENTION_POLICY | v1 | ACTIVE | hygiene/retention.py | hygiene worker, DB owners |
| BEHAVIOR_ANALYSIS | v1 (behavior-v1) | ACTIVE | intelligence/behavior.py | reporting, web, telegram |
| ANOMALY_EVENT | v1 (anomaly-v1) | ACTIVE | intelligence/behavior.py | reporting, web, telegram |
| DB_MIGRATION | v1 | ACTIVE | database/engine.py | startup, cli, updater, health, web |
| SCHEMA_MANIFEST | v1 | ACTIVE | database/manifest.py | database/engine.py, cli, health |

## Contract detail template

Every contract entry in this registry should document:
- version
- purpose
- required fields
- immutable fields
- derived fields
- producer
- consumers
- compatibility rules
- invariants

## EXIT_CLASSIFICATION v3 — evidence-provenance contract (TASK-3 / BUG-088)

- Purpose: every `exit_mechanism` value must be traceable to the evidence
  that produced it — never presented as broker-proven when it was inferred.
- Producer: `experience/outcome_recovery.py::classify_exit_with_evidence`
  (returns reason, evidence_source, evidence_detail, confidence).
- Consumers: `order_manager` (ledger + telegram), `accounting/normalize.py`
  (`_classify_stop` geometry remains the accounting-side re-check).
- Evidence sources: ENGINE_FORCED / BROKER_DEAL_REASON /
  BROKER_DEAL_COMMENT / SL_GEOMETRY / TP_GEOMETRY / FALLBACK_HEURISTIC.
- MT5 reason codes (authoritative): DEAL_REASON_CLIENT=0/1/2,
  DEAL_REASON_EXPERT=3, DEAL_REASON_SL=4, DEAL_REASON_TP=5, DEAL_REASON_SO=6,
  ROLLOVER=7, VMARGIN=8, SPLIT=9. reason 4 is SL, never TP (BUG-083 fix).
- Persisted on the closing autopsy row: `exit_reason_source`,
  `exit_evidence`, `exit_reason_confidence`.
- Immutable fields: exit_mechanism, exit_reason_source, exit_evidence.
- Invariant: UNKNOWN evidence stays UNKNOWN (INV-012) — reason 0 without
  corroboration is UNKNOWN, never promoted to MANUAL.

## Notes / To-Do
- Expand each row above into a full entry (required fields, immutable/derived, compatibility, invariants) as contracts are touched.
- BUG-054 payload contract (8 fields) and BUG-081 exit-classification semantics are the first candidates for full entries.

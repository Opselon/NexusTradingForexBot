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
| MODEL_MANIFEST | v1 | ACTIVE | model_lifecycle/ | training, runtime |
| ACCOUNTING_SNAPSHOT | v1 | ACTIVE | accounting/ | web, telegram |
| EXIT_CLASSIFICATION | v2 (evidence precedence) | ACTIVE | execution/ | accounting, experience |
| RESEARCH_RESULT | v1 | ACTIVE | research/ | registry, web |
| UI_STATE | v1 (900-bar standard) | ACTIVE | web/ | dashboard |

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

## Notes / To-Do
- Expand each row above into a full entry (required fields, immutable/derived, compatibility, invariants) as contracts are touched.
- BUG-054 payload contract (8 fields) and BUG-081 exit-classification semantics are the first candidates for full entries.

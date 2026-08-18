# Architecture Dependency Map — Nexus Scalp Engine (NSE)

> Maintained per the MASTER MULTI-AGENT CONTRACT §9 (see `agents/multi-agent-git-contract.md`).
> Describes actual subsystem relationships (verified against executable code; see agents/skill.md for the full inventory).
> Every edge documents: contract, direction, expected data, safety guarantees.

## Canonical high-level flow

```text
MT5
  │  (tick/order/position/account events)
  ▼
MT5 Adapter / Providers            [ports: IMT5Port; adapters: mt5/, paper/, remote]
  │  (normalized events)
  ▼
LiveEngine (async event loop)      [src/nexus_scalp/application/live_engine.py]
  ├─ Feature Engine                [features/scalp_features.py → FEATURE_VECTOR_50D]
  ├─ Model Runtime                 [models/scalp_net.py → MODEL_MANIFEST; Champion/Challenger]
  ├─ News Context                  [news/ → NEWS_CONTEXT]
  ├─ Experience Gate               [experience/ → TRADE_OUTCOME / EXIT_CLASSIFICATION]
  ├─ RiskEngine                    [risk/risk_engine.py → risk boundaries, INV-003]
  └─ OrderManager                  [execution/order_manager.py → TRADE_EXECUTION_CONTEXT, INV-004]
        │
        ▼
   Execution Outcome               [broker fills; parent-child lineage, INV-005/006]
        │
        ▼
Ledger Experience + Accounting     [artifacts/audit.db; accounting/ → ACCOUNTING_SNAPSHOT]
        │
        ▼
Research / Backtest                [research/ + strategies/ → STRATEGY_CANDIDATE / RESEARCH_RESULT]
        │
        ▼
Dashboard / Telegram               [web/ + Web/ → UI_STATE; telegram read-only, INV-010]
```

## Edge contracts

| Edge | Contract | Direction | Expected data | Safety guarantees |
| :--- | :--- | :--- | :--- | :--- |
| MT5 → Adapters | MT5_BROKER_SNAPSHOT | broker → engine | ticks, rates, positions, orders, account | broker truth precedence (INV-011) |
| Adapters → LiveEngine | normalized events | adapter → app | normalized ticks/bars | no sync DB on tick path (INV-001) |
| LiveEngine → Features | FEATURE_VECTOR_50D | app → features | 50 floats, finite, [-3,3] | schema-controlled ordering (INV-009) |
| LiveEngine → Model | MODEL_MANIFEST | app → models | (Batch,50) → 4 logits | Champion never overwritten |
| News → LiveEngine | NEWS_CONTEXT | news → app | context bundle | cache-only on hot path |
| LiveEngine → RiskEngine | proposal → risk | app → risk | TradeProposal | authoritative risk boundaries (INV-003) |
| RiskEngine → OrderManager | sized proposal | risk → execution | lot, SL/TP, margin-safe | HARD_MAX_LOTS / MAX_TOTAL_EXPOSURE |
| OrderManager → Broker | TRADE_EXECUTION_CONTEXT | execution → MT5 | orders/fills | parent-child lineage (INV-005) |
| Execution → Ledger/Accounting | TRADE_OUTCOME / EXIT_CLASSIFICATION | execution → audit.db | outcomes, classifications | UNKNOWN stays UNKNOWN (INV-012) |
| Ledger → Research | RESEARCH_RESULT | audit.db → research | experiences/outcomes | historical experience immutable (INV-007) |
| Ledger → Dashboard/Telegram | ACCOUNTING_SNAPSHOT / UI_STATE | audit.db → web | snapshots, reports | telegram read-only (INV-010) |

## Ownership (contract §3)

LiveEngine → Hermes-Runtime · OrderManager/Execution → Hermes-Execution ·
RiskEngine → Hermes-Risk · MT5 Adapter/Providers → Hermes-MT5 ·
Experience/Learning → Hermes-Learning · Research/Backtesting →
Hermes-Research · News → Hermes-News · Accounting → Hermes-Accounting ·
Model → Hermes-Model · Web/Dashboard → Hermes-UI · Release → Hermes-Release.
Cross-owner changes MUST be tagged `CROSS-OWNER CHANGE` with justification.

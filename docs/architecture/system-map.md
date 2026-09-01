---
title: System Map
description: Component inventory of the Nexus Scalp Engine — packages, databases, workers, and the dependency shape.
lang: en
---

# System Map

## Package inventory (`src/nexus_scalp/`)

```text
nexus_scalp/
├── domain/            frozen Pydantic contracts (TickData, TradeProposal, …)
├── ports/             IMT5Port · IGatewayPort        ← the hexagon seam
├── adapters/          mt5/ (Win32 IPC) · gateway (ZMQ) · paper/ · database/ (AuditRepository, WAL)
├── features/          scalp_features (50D) · schema registry · schema_contract (70D SSoT)
│                      liquidity_engine · regime_classifier · temporal (research)
├── models/            ScalpNet (dual-path TCN + self-attention)
├── training/          walk_forward_trainer · labeling/ (triple-barrier)
├── signals/           policy · rule matrix (~30 SMC rules)
├── strategies/        factory (+ provider_gate for LLM/AI services)
├── risk/              RiskEngine (Kelly sizing, clamps, kill switch)
├── execution/         order_manager (+ protection_ledger · position_state_machine ·
│                      recovery_budget extracted seams)
├── accounting/        core · aggregation · retention
├── application/       live_engine (async orchestrator)
├── experience/        ledger · outcome_recovery · evaluator (autopsy intelligence)
├── intelligence/      behavior detection · lifecycle · store
├── mslie/             market-structure perception (advisory)
├── research/          dataset · discovery · pipeline · backtest · walkforward · oos ·
│                      streaming_replay · forward_test · counterfactual · evidence · observability
├── model_generation/  artifact-first factory (datasets/experiments/models + manifests)
├── model_lifecycle/   candidate staging, champion/challenger
├── governance/        14-gate verify · promotion transaction · rollback preview
├── shadow/            shadow70 runtime (zero order authority)
├── news/              ingest · analysis · bounded gate · database (schema/articles/analysis/queries)
├── incidents/         correlation · lineage · impact · reports · worker
├── forensics/         health engine · deploy gate · experience_gap · trend
├── hygiene/           retention · quarantine · consistency · index health
├── observability/     logging (severity-split) · telegram notifier (read-only)
├── web/               server + domain route modules · debug_snapshot · db_console
├── release/           CLI shim · updater · verifier · packaging
├── settings/          service (settings DB authority) · secret_store
└── configuration/     AppConfig (bootstrap) · RuntimeConfigStore (authoritative snapshots)
```

## Databases (SQLite, WAL)

| DB | Responsibility |
| :--- | :--- |
| `artifacts/audit.db` | trading ledger, experience outcomes, accounting, research registry, strategy lifecycle, incidents |
| `artifacts/news.db` | news ingestion, analysis, consensus, impacts |

Writes queue through `AuditRepository`'s background worker thread — the tick
path never waits on the database (INV-001). Migrations are additive and
checksummed (`nexus db`); hygiene is AUDIT_ONLY by default.

## Workers (all off the tick hot path)

- `AuditRepository` background writer thread
- Research worker (dataset → discovery each cycle)
- Training worker via `asyncio.to_thread` (auto-train disabled by default)
- Hygiene worker (light 30m / deep 6h cadence, config-driven)
- Incident worker (state machine STARTING/RUNNING/DEGRADED/FAILED)
- Telegram notifier (read-only outbound, INV-010)

## Model artifacts

`artifacts/models/…` + `artifacts/model_generation/…` — versioned bundles
(model, scaler, manifest with schema hash + dataset ID + git commit). The
10-gate load gate validates every bundle before attach; width/hash mismatch
blocks loudly. See [Model pipeline](model-pipeline.md).

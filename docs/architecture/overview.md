---
title: Architecture Overview
description: Hexagonal (ports-and-adapters), event-driven architecture of Nexus Scalp Engine — layers, boundaries, and why they exist.
lang: en
---

# Architecture Overview

NSE is **hexagonal (ports-and-adapters) and event-driven**. Broker platforms,
models and network adapters are isolated behind port contracts
(`IMT5Port`, `IGatewayPort`), so the domain never knows which broker — or
whether a broker — is attached.

```text
        ┌────────────────────────  ports (contracts)  ───────────────────────┐
        │                                                                    │
 Market │  ┌──────────┐   ┌──────────┐   ┌─────────┐   ┌────────┐            │
 data ──┼─►│ features │──►│  model   │──►│ signals │──►│  risk  │            │
 (MT5 / │  │ 50D/70D  │   │ ScalpNet │   │ policy  │   │ engine │            │
 paper) │  └──────────┘   └──────────┘   └─────────┘   └───┬────┘            │
        │                                                  ▼                 │
        │                                          ┌──────────────┐          │
        │                                          │  execution   │          │
        │                                          │ OrderManager │          │
        │                                          └──────┬───────┘          │
        ▼                                                 ▼                  │
 ┌──────────────┐   ┌──────────────┐   ┌──────────────────────────────┐       │
 │  adapters    │   │  accounting  │   │  application (LiveEngine)    │       │
 │ mt5/paper/db │   │ SQLite WAL   │   │  async event loop, web       │       │
 └──────────────┘   └──────────────┘   └──────────────────────────────┘       │
        └────────────────────────────────────────────────────────────────────┘
```

## Layers

| Layer | Location | Responsibility | Hard boundary |
| :--- | :--- | :--- | :--- |
| Domain | `src/nexus_scalp/domain/` | Immutable Pydantic contracts (`TickData`, `TradeProposal`, `Position`, `AccountInfo`) | frozen models; `.model_copy(update=…)` |
| Ports | `src/nexus_scalp/ports/` | `IMT5Port`, `IGatewayPort` protocol interfaces | signature change ⇒ all adapters update |
| Adapters | `src/nexus_scalp/adapters/` | MT5 Win32 IPC, ZMQ remote gateway, paper simulator, SQLite WAL `AuditRepository` | **no sync DB on the hot path** |
| Features | `src/nexus_scalp/features/` | 50D base → 70D canonical assembly, schema registry + hash, regime | schema is SSoT; ordering is contract |
| Models | `src/nexus_scalp/models/` | ScalpNet dual-path (2D snapshot / 3D TCN+attention), 4-logit head | input dim must equal schema dim |
| Training / Labeling | `src/nexus_scalp/training/`, `labeling/` | purged walk-forward, triple-barrier labels (Polars) | embargo/purge required; no lookahead |
| Signals / Strategies | `src/nexus_scalp/signals/`, `strategies/` | policy, ~30-rule SMC matrix, factory | **never holds adapter or risk handle** |
| Risk | `src/nexus_scalp/risk/` | dynamic volume, margin clamps, tier caps | authoritative for boundaries |
| Execution | `src/nexus_scalp/execution/` | OrderManager: 60-scenario router, 11 position states | authoritative for dispatch; `HARD_MAX_LOTS=10` |
| Accounting | `src/nexus_scalp/accounting/` | ledger, PnL, market calendar, retention | historical rows immutable |
| Application | `src/nexus_scalp/application/` | `LiveEngine` async loop: tick → features → … → web | never block the event loop |
| Intelligence closed loop | `experience/`, `intelligence/`, `mslie/`, `research/`, `shadow/`, `news/`, `model_lifecycle/`, `governance/` | autopsy, behavior detection, candidate training, shadow, promotion gates | **zero order authority**; promotion operator-gated |
| Model Factory | `src/nexus_scalp/model_generation/` | artifact-first datasets/experiments/models with manifests | inference needs no DB |
| Observability / Ops | `observability/`, `hygiene/`, `incidents/`, `forensics/`, `settings/` | structured logs, hygiene (AUDIT_ONLY default), incidents, deploy gate | incidents never mutate trading/risk/models |
| Web/API | `src/nexus_scalp/web/`, `Web/` | FastAPI REST + SSE + WebSocket, buildless SPA Control Center | background tasks registered in `app.state` |
| Release | `src/nexus_scalp/release/` | installer/CLI surface, update/rollback, verify | update blocked while LIVE |

## The closed intelligence loop

Live trade → accounting → experience → autopsy → research → candidate
training → shadow comparison → (operator-gated) promotion. Everything is
rebuildable from immutable ledgers.

## Where to look next

- [System map](system-map.md) — component inventory
- [Data flow](data-flow.md) — tick → decision, end to end
- [Runtime](runtime.md) — the LiveEngine loop
- [Research stack](research-stack.md) — how candidates are made and judged
- Authoritative internal map: [`agents/skill.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/skill.md)

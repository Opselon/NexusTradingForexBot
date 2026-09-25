---
title: Architecture
description: Hexagonal, event-driven architecture — layers, boundaries, and the tick-to-decision path.
lang: en
---

NSE is **hexagonal (ports-and-adapters) and event-driven**: broker platforms,
models and network adapters sit behind port contracts (`IMT5Port`,
`IGatewayPort`); the domain never knows which broker is attached.

```text
Market Data (MT5 / ZMQ / paper)
  → Causal Features — 50D base (scalp_v1) · governed 70D assembly
  → Inference Validator — schema hash · scaler dim · bounds (loud rejection)
  → ScalpNet (TCN + self-attention) — 4 logits → confidence gate
  → Regime Guardian → SMC Policy Matrix (~30 rules)
  → Risk Engine (Kelly sizing · margin ≤20% · tier caps · HARD_MAX_LOTS=10)
  → OrderManager (60-scenario router · 11 position states · atomic teardown)
  → IMT5Port adapter → MT5 / paper / shadow (zero order authority)
  → Accounting (immutable SQLite WAL) → Experience & autopsy
  → Observability (logs · incidents · forensics) + Control Center (REST/SSE/WS)
```

## Layers

| Layer | Responsibility | Hard boundary |
| :--- | :--- | :--- |
| Domain | frozen Pydantic contracts | never mutate |
| Ports/Adapters | MT5 Win32 IPC · ZMQ · paper · SQLite WAL repo | no sync DB on hot path |
| Features | 50D/70D causal engine · schema registry + hash | ordering is contract |
| Models/Training | ScalpNet · purged walk-forward · triple-barrier | no lookahead |
| Signals | policy · SMC rule matrix | no order authority |
| Risk / Execution | sizing boundaries · 60-scenario dispatch | authoritative clamps |
| Intelligence loop | experience · research · shadow · governance | zero order authority |
| Observability | logs · incidents · forensics | diagnostic-only |

## Key properties

- **INV-001** — zero sync DB/training/network on the tick path.
- **INV-002** — learning components physically cannot place orders.
- **INV-008** — no lookahead; liquidity strictly causal.
- **INV-011** — broker truth wins when reconciling exposure.
- Model bundles pass a **10-gate load gate** at every attach; width/hash
  mismatch blocks loudly, never silently.

Deep dive: [overview.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/architecture/overview.md)
· [data-flow.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/architecture/data-flow.md)
· [model-pipeline.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/architecture/model-pipeline.md)
· internal authoritative map: [`agents/skill.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/skill.md).

# 01 — Architecture Map (mental model)

## The system in one paragraph

Nexus Scalp Engine is a hexagonal, event-driven quantitative scalping
runtime. A broker adapter (MT5 direct / remote gateway / paper simulation)
feeds ticks into LiveEngine's async loop; the loop slices ticks into M1
bars, computes a 50-dimensional causal feature vector (extended to a 70D
research contract with news + liquidity families), and passes it through a
strict gate cascade — regime guardian → neural inference (ScalpNet) →
signal policy → experience gate → intelligence gate → news gate → risk
engine → order manager — before anything reaches the broker. Every
decision, rejection, and outcome is persisted to a queued SQLite audit
layer, and a fleet of background workers (accounting, intelligence,
research, factory, training, shadow, news, incident, hygiene) turn that
history into strategy intelligence, candidate models, and reports without
ever touching the tick path.

## Layer map (with dependencies)

```
Domain (frozen Pydantic contracts)
   └── Ports (IMT5Port / IGatewayPort — dependency inversion)
         └── Adapters (DirectMT5 / RemoteGateway / Paper / SQLite audit repo)
               └── Features (50D scalp_features, regime, 60/70/92D family)
                     └── Models (ScalpNet 2D/3D dual path)
                           └── Signals (policy + rule matrix + stability)
                                 └── Risk (dynamic sizing, impact, margin)
                                       └── Execution (OrderLifecycleManager)
                                             └── Application (LiveEngine async)
                                                   └── Web/FastAPI + UI + CLI
Cross-cutting: Configuration (runtime-config hot reload), Observability
(structlog + Telegram), Settings (DPAPI secret store), Database (migrations),
Governance (model gate/promotion), Shadow (challenger evaluation),
Research/Strategies, Model Lifecycle/Generation (artifact-first factory),
Accounting/Reporting, Experience/Intelligence, News, Candle Intelligence,
Incidents, Forensics, Hygiene, Release/Update.
```

## Design principles distilled from the code

1. NO FAKE DATA — every unavailable metric is None/unavailable-provenance,
   never a fabricated 0 (broker snapshots carry source=UNAVAILABLE;
   accounting renders n/a; Debug console shows NOT_EXPOSED).
2. NO FUTURE LEAKAGE — causal feature windows, ±5 fractal confirmation,
   purge+embargo labeling, tick-timestamp clocks, UTC normalization.
3. NEVER BLOCK THE TICK PATH — queued DB writes, to_thread training,
   TTL-cached gates, worker isolation, failure-isolated hooks (INV-001/018).
4. NO ORDER AUTHORITY OUTSIDE EXECUTION — research/strategies/news/candle
   are advisory; only OrderLifecycleManager + the adapter can touch the
   broker.
5. FAIL CLOSED — invalid input → 0.0/None/reject with reason code,
   never a silent pass; contract drift raises at import/test time.
6. ONE CANONICAL SOURCE PER TRUTH — accounting core for PnL; features/
   schema_contract for geometry; settings service for credentials;
   runtime config store for hot values; broker state for connection truth.
7. OBSERVABILITY IS A FIRST-CLASS LAYER — EXEC-... trace ids, per-call
   IPC diagnostics, latency tracers, SSE diag, incident correlation,
   Telegram lifecycle logs.
8. The 70D series is CANDIDATE-ONLY — ACTIVE_SCHEMA_ID stays scalp_v1;
   promotion is a governed, evidence-based decision (never automatic).
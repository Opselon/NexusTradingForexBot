---
title: Runtime — the LiveEngine Loop
description: How the async runtime behaves at runtime — tick pipeline, workers, modes, and what may never block.
lang: en
---

# Runtime — the LiveEngine loop

`application/live_engine.py` is the orchestrator: a single async event loop
that drives the tick pipeline and coordinates background workers.

## Startup sequence

1. Launcher binds an adapter (`DirectMT5Adapter` / `RemoteMT5GatewayAdapter` /
   paper) behind `IMT5Port`.
2. Pre-flight **doctor gate**: MT5 terminal present? config valid? platform
   supported? — otherwise the engine refuses to start.
3. Migration gate: canonical DB migration engine runs (additive, checksummed).
4. Model load: Champion bundle validated by the **10-gate load gate** (manifest
   hash, scaler dim, schema family, width contract…).
5. Mode select: **PAPER (default)** / SHADOW / LIVE (interactive confirmation).
6. Web server: FastAPI Control Center + SSE + WebSocket.
7. Workers start (audit writer, research, hygiene, incidents, Telegram).

## Tick pipeline (per tick)

```text
on_tick → normalize → features (50D) → [governed 70D assembly] → inference
        → regime → policy → risk → execution dispatch → state sync
```

Invariants riding on this loop:

- **INV-001** — zero sync DB / training / network on the tick path.
- **INV-004** — OrderManager is the only dispatch authority.
- **INV-011** — broker truth wins when reconciling exposure.
- Record builders for online learning route through ONE canonical builder and
  refuse (`None`) when the feature snapshot is not VALID — never zero-fill.

## Background workers (off-path)

Audit writer (queued WAL writes), research worker, training worker
(`asyncio.to_thread`, disabled by default), hygiene worker, incident worker,
Telegram notifier (outbound only).

## Runtime modes

| Mode | Order authority | Purpose |
| :--- | :--- | :--- |
| paper *(default)* | simulated fills | development, UI, CI |
| shadow | **none** (`simulated=True`) | live-data evaluation without risk |
| live | real broker | explicit interactive confirmation; full risk panel printed |

## Teardown

Foreground runs stop with `Ctrl+C` (graceful teardown); `nexus stop` handles
`--daemon` pidfiles honestly (BUG-172: dead-pid reported as such). Updates are
blocked while LIVE.

## Deep dives

- [Execution pipeline](execution-pipeline.md)
- [Observability](observability.md)
- Internal: `docs/architecture/order_manager_architecture.md`

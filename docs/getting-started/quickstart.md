---
title: Quickstart
description: Minimal verified path from clone to a running engine — PAPER mode by default, never LIVE silently.
lang: en
---

# Quickstart

The shortest safe path. Every command below is part of the verified CLI surface
(`nexus help` shows the authoritative list).

```bash
# 1. Install (developers, from source)
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -e .[dev]

# 2. Verify your system
nexus doctor          # read-only diagnostics, 19 categories + suggested fixes
nexus health          # READY / DEGRADED / NOT READY

# 3. Run — PAPER mode is the default, never LIVE silently
nexus start           # paper simulation, Control Center at http://127.0.0.1:8080

# 4. Evaluate on real data with zero order authority
nexus start --mode shadow

# 5. Stop
nexus stop            # for --daemon runs; Ctrl+C in the foreground
```

## Mode semantics

| Mode | Data | Orders | Use |
| :--- | :--- | :--- | :--- |
| `paper` *(default)* | simulated | simulated | first run, UI, development |
| `shadow` | live feed | **none — zero order authority** (`simulated=True`) | evaluate model/signals on real market data |
| `live` | live feed | real | **explicit interactive confirmation required**; full risk panel printed first |

> [!WARNING]
> This engine can place **real trades with real money** in LIVE mode. The
> recommended first-run progression is
> [Demo MT5 account → SHADOW → small LIVE](first-run.md). The engine's hard
> clamps protect the strategy — not your capital from market volatility.

## What you should see

`nexus start` boots the engine in PAPER mode, runs pre-flight doctor + migration
checks, and serves the **Control Center** (FastAPI) at `http://127.0.0.1:8080`
(`--port` to change): REST `:8080/api/...`, tick stream over SSE
(`/api/ticks/stream`), live dashboard over WebSocket (`/web`).

Docker alternative: `docker compose up -d --build` (PAPER by default, dashboard
at `http://localhost:9090`, `/health` readiness). See
[Common workflows](../guides/common-workflows.md).

## Where to go next

- [First-run safety](first-run.md) — Demo → Shadow → Live ladder
- [CLI guide](../guides/cli.md) and [CLI reference](../reference/cli-reference.md)
- [How the engine works](../architecture/overview.md)

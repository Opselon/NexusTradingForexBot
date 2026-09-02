---
title: API Reference
description: The live REST/SSE surface of the running engine — 203 /api endpoints across 22 groups, plus real examples and contracts.
lang: en
---

# API Reference

The Control Center backend is a FastAPI application exposing **203 `/api/*`
endpoints across 22 groups** (verified by scanning the route modules in
`src/nexus_scalp/web/`), plus Server-Sent Events for ticks and WebSocket for
the dashboard. Everything the UI does, the API does — the UI is a consumer of
the same surface.

> [!NOTE]
> Endpoint counts are generated from the repository's actual route decorators —
> they drift with the code, and the docs doctor flags stale counts.

## Discovery

- **Health / readiness:** `GET /health` → `200` with `READY`/`DEGRADED`, `503` when NOT READY.
- **Engine state:** `GET /api/status` and `GET /api/live/state` (the dashboard heart, includes the effective serving contract).
- **OpenAPI:** the FastAPI app serves its schema at `/openapi.json` and Swagger UI at `/docs` on the same port as the UI (`http://127.0.0.1:8080` by default; Docker `:9090`).

## Endpoint groups (verified)

| Group | Count | What it covers |
| :--- | ---: | :--- |
| `/api/models/*` | 45 | governance, promotion/rollback previews, load gate, model inventory |
| `/api/research/*` | 23 | datasets, discovery, runs, evidence, observability, retry/cancel |
| `/api/news/*` | 18 | ingestion, analysis, consensus, keywords, bounded gate state |
| `/api/db/*` | 12 | migrations, hygiene runtime + quarantine, console, portability |
| `/api/account/*` | 11 | equity curve, drawdown, growth, performance intelligence |
| `/api/debug/*` | 10 | canonical 18-section debug snapshot, traces |
| `/api/diagnostics/*` | 10 | read-only incident diagnostics, export |
| `/api/intelligence/*` | 10 | behavior detections, anomalies, lifecycle |
| `/api/command-center/*` | 8 | operator command surfaces |
| `/api/operator/*` | 6 | operator actions (gated) |
| `/api/replay/*` | 6 | historical session replay over the shared engine |
| `/api/experience/*` | 5 | autopsy, outcome recovery |
| `/api/runtime-config/*` | 4 | authoritative runtime snapshots (hot reload) |
| `/api/settings/*` | 4 | settings DB (secrets never returned) |
| `/api/liquidity/*` | 3 | liquidity state/features/toggle (research governor) |
| `/api/forensics/*` | 2 | health engine, deploy gate |
| others (`rules`, `live`, `engine`, `config`, `mslie`, `algo`, …) | rest | rules matrix, SSE streams, algo tuner |

## Conventions

- **JSON everywhere.** Enums serialize via a single `serialize_enums()` helper; error payloads use a shared safe-error shape (no stack traces, no internals).
- **Timestamps** are broker-local-offset-aware ISO-8601 (the broker epoch quirk is a documented contract, not an accident).
- **No authentication on loopback** (the API binds `127.0.0.1` by default); secrets never appear in responses — provider URLs are `[REDACTED_SECRET]`.
- **Write paths are gated:** LIVE-affecting actions require explicit operator confirmation; `/api/factory/provider-test` performs exactly one bounded probe through the provider gate.

## Examples

```bash
# engine health
curl http://127.0.0.1:8080/health

# account performance intelligence (compact intelligence block included)
curl http://127.0.0.1:8080/api/account/performance/intelligence

# forensic health verdicts
curl http://127.0.0.1:8080/api/forensics/health

# debug snapshot: 18 canonical sections, contract validation included
curl http://127.0.0.1:8080/api/debug/state

# live tick stream (SSE)
curl -N http://127.0.0.1:8080/api/ticks/stream
```

## Automation notes

- All list endpoints are stable-shape JSON; the Control Center's `api_client.js` is the reference client.
- SSE clients should reconnect on drop; stream state is idempotent.
- Runtime configuration reads must go through `GET /api/runtime-config/*` snapshots — cached constructor values lie.

## Where the code lives

Route modules: `src/nexus_scalp/web/` (`server.py` + domain modules:
`debug_research_routes.py`, `diagnostics_state_routes.py`,
`news_liquidity_mslie_routes.py`, `factory_routes.py`, `db_console.py`, …).
Contracts are indexed in
[`agents/contracts.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/contracts.md).

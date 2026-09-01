---
title: Configuration
description: How Nexus Scalp Engine configuration is structured — AppConfig bootstrap, runtime snapshots, settings DB, and the no-secrets-on-disk rules.
lang: en
---

# Configuration

Configuration has three layers with different authorities. Mixing them up is a
classic failure mode — read this page before editing YAML by hand.

## 1. `AppConfig` (bootstrap / import / export)

`src/nexus_scalp/configuration/config.py` — loads the YAML contract
(`configs/base.yaml` is the canonical base; `configs/live.yaml.example` is the
live-shaped example). Used for bootstrap/import/export only.

## 2. Runtime configuration (authoritative live state)

`RuntimeConfiguration` via `RuntimeConfigStore.get_snapshot()` is the
**authoritative live state** — versioned, hot-reloadable. Consumers must read
through `get_snapshot()`, never cached constructor values. Scope tags decide
when a change applies (`LIVE_IMMEDIATE` vs `NEXT_DECISION`).

## 3. Settings DB (secrets + user intent)

Credentials (e.g. Telegram) live in the settings database via
`settings_service.set_telegram()` — **never** in `live.yaml` and never committed
(INV-010). The UI save path routes through the same service.

## Key risk-relevant keys

| Key | Meaning |
| :--- | :--- |
| `risk.max_concurrent_positions` | exposure bound (first run: `1`) |
| `risk.risk_per_trade_pct` | fractional-Kelly input, per-trade risk |
| `risk.max_account_drawdown_pct` | drawdown stop |
| `liquidity_features_enabled` | 70D liquidity block governor (research path) |

## Validation

```bash
nexus config                # inspect active configuration (secrets masked)
nexus config --validate path/to/config.yaml
```

Invalid config or a failing pre-flight doctor check blocks engine start.

## Docker

`docker-compose.yml` + `.env.example` define the container env contract (safe
defaults, no secrets). See [`docs/docker.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/docker.md).

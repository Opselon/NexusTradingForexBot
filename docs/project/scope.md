---
title: Scope
description: What Nexus Scalp Engine is and is not — explicit in-scope and out-of-scope boundaries.
lang: en
---

# Scope

## In scope

- A complete quantitative trading **pipeline**: data adapters (MT5/paper/gateway),
  causal feature engineering (50D active contract; 60D/70D research family),
  deep model inference (ScalpNet TCN + self-attention), SMC policy matrix,
  invariant risk engine, execution with 60-scenario router, SQLite WAL
  accounting ledger, experience/autopsy intelligence, research & validation
  (walk-forward, OOS gate, robustness), replay and shadow runtimes, model
  governance, observability (structured logs, incidents, forensics, Telegram),
  FastAPI Control Center UI, packaged Windows release + installer + update/rollback.
- **Multi-agent engineering memory**: architecture map, bug ledger, runtime
  invariants, contracts, taskboard, decision records — treated as first-class
  repository artifacts.

## Out of scope (by design)

- Multi-broker support beyond the MT5 surface (Win32 IPC / ZMQ remote gateway /
  paper simulator).
- Web/mobile trading frontends other than the bundled Control Center.
- Guaranteed profitability, signal selling, or managed accounts.
- Non-Windows packaged releases (Linux is developer/Docker via the remote
  gateway adapter only; ARM64 is explicitly unsupported).
- Cloud SaaS deployment of the trading engine.

## Adjacent systems (interfaces only)

- **MetaTrader 5 terminal** — external; the engine integrates through
  `IMT5Port` adapters and refuses to start without a healthy terminal.
- **News feeds (opt-in)** — RSS/Atom ingestion with a bounded gate that can
  never force a trade or bypass risk.
- **Telegram** — read-only observability channel (INV-010).

## Where boundaries are documented

Subsystem-level ownership and locked paths live in
[`agents/locks.yaml`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/locks.yaml)
and the [contribution guide](../contributing/contribution-guide.md).

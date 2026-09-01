---
title: Vision & Why
description: Why Nexus Scalp Engine exists — the problem, the philosophy, and the evidence-first engineering culture.
lang: en
---

# Vision — why Nexus exists

## The problem

Retail-grade algorithmic trading projects usually fail in one of two ways:

1. **They hide the truth.** Metrics are fabricated or optimistic, failures are
   silent, and the gap between backtest and runtime is never measured — so the
   system's own reporting cannot be trusted.
2. **They leak the future.** Feature engineering, labeling or "research" uses
   information that would not have been available at decision time, so results
   are structurally invalid no matter how good they look.

Nexus Scalp Engine (NSE) was built specifically against both failure modes. It
is an attempt to construct a **complete, auditable pipeline** — market data →
features → model → policy → risk → execution → accounting → research — where
every stage is observable, every identity is fingerprinted, and every claim can
be traced to evidence.

## The philosophy (repository-backed)

These principles are enforced in code and contracts, not just stated:

- **Evidence before claims.** Metrics without underlying evidence render
  `n/a` — never fake zeros. Runtime claims are graded (CODE / TEST /
  INTEGRATION / LIVE / RELEASE VERIFIED).
- **No lookahead.** Purged + embargoed walk-forward (Lopez de Prado),
  strictly causal features (liquidity confirmation bars, completed HTF buckets
  only), broker history REPLACE+ALIGN — INV-008.
- **Causal parity.** Live = replay = training feature semantics. The same
  feature contract (`scalp_v1` 50D active; 70D `scalp_v3` canonical research
  contract) with schema hashing; replay must be bit-exact vs dataset.
- **Runtime truth.** Settings intent vs runtime gate are separate authorities;
  broker truth wins over stale local state (INV-011); historical ledger rows
  are immutable (INV-007).
- **Layered architecture.** Hexagonal ports-and-adapters: `IMT5Port` isolates
  broker IPC; research/learning workers hold no order authority (INV-002).
- **Failure isolation.** The live tick hot path never blocks on analytics, DB
  or training (INV-001); incidents are diagnostic-only and never mutate
  trading/risk/models.
- **Validation before promotion.** OOS failure ⇒ REJECTED regardless of
  in-sample performance; promotion is strictly operator-gated; candidates never
  promote themselves.

## Current status (truthful)

NSE is a **production-hardened runtime with an honest research posture**: the
packaged release pipeline is real and published (v9.0.x tags), live MT5
execution works with an account-identity fail-safe, and the closed research
loop exists — while the 70D research series remains **candidate-only with
negative OOS evidence so far**, and the live contract deliberately stays 50D.
Nothing is auto-promoted. See [Project Status](status.md) for the full
certification matrix.

## What this project is not

- Not a signal-selling service, not an investment product.
- Not a guaranteed-profitable system — the repository's own evidence (OOS
  rejections, counterfactual studies) is published rather than hidden.
- Not a black box: the entire engineering memory — architecture map, bug
  ledger, invariants, decision records — lives in the open repository.

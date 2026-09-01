---
title: Data Flow — Tick to Decision
description: The canonical data path from a broker tick to an audited trading decision, with every contract boundary.
lang: en
---

# Data Flow — from tick to decision

This is the canonical path (from `agents/skill.md` §4). Every arrow is a
contract boundary.

```text
TickData (MT5 / paper / gateway)
  → ScalpFeatureEngine.to_tensor_input()
      50D base (scalp_v1 · FEATURE_NAMES · [-3,+3] · finite)
  → assemble_70d / build_70d_vector()
      70D canonical: Base 0..49 | News 10D 50..59 | Liquidity 10D 60..69
  → InferenceContractValidator
      scaler dim == feature dim · schema hash · finite · bounds
  → ScalpNet → 4 logits (NO_TRADE / BUY / SELL / WAIT) → confidence gate 0.35
  → regime_classifier (Regime Guardian)
  → signals/policy + rule_matrix (~30 rules, TTL 5s cache)
  → RiskEngine.calculate_dynamic_volume() + evaluate_proposal()
      free-margin 20% clamp · tier caps · HARD_MAX_LOTS
  → OrderManager (60-scenario router · 11 position states ·
      MAX_TOTAL_EXPOSURE=1 · 30s re-quote lock · 1.0×ATR drift)
  → broker (IMT5Port adapter: Win32 IPC / ZMQ / paper)
  → accounting (TradeOutcome / ACCOUNT_SNAPSHOT) → AuditRepository (queued, WAL)
  → web SSE/WebSocket + Telegram (read-only) + experience/research/shadow
```

## Contract boundaries in the flow

| Boundary | Enforced by | Failure mode |
| :--- | :--- | :--- |
| feature dim / ordering | schema registry + `schema_contract` hash | loud rejection (`FEATURE_CONTRACT_MISMATCH`), never silent padding |
| scaler ↔ features | `InferenceContractValidator` | `SCALER_MISMATCH` blocks inference |
| confidence semantics | policy gate measures trained-class directional share (CHG-0042) | miscalibrated confidence cannot masquerade as direction |
| sizing limits | `RiskEngine` + OrderManager clamps | order refused |
| order authority | ports/adapters isolation + INV-002 | research/learning components physically cannot place orders |
| persistence | `AuditRepository` background writer | hot path never blocks (INV-001) |

## Hot-path discipline (INV-001)

The tick pipeline performs **zero synchronous I/O**: no DB queries, no
training, no network on the tick path. Bar aggregation and broker snapshots
are cached off-path; news context is cache-only.

## Assembly rules for the 70D block

Missing sub-blocks require **explicit neutral vectors** (`FEATURE_DISABLED`) —
silent fabrication is forbidden; unavailable features block
(`FEATURE_UNAVAILABLE`). Reordering features changes the schema hash, which
invalidates models — by design.

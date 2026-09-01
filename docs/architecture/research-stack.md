---
title: Research Stack
description: How the research engine turns historical data into validated (or rejected) strategy candidates.
lang: en
---

# Research Stack

The research side answers one question with evidence: **is this candidate good
enough to trust — and can we prove it on data it never saw?**

## Pipeline

```text
DATA (canonical tick/bar datasets, provenance-fingerprinted)
  → FEATURES (same causal contract as live: 50D base, governed 70D assembly)
  → LABELING (triple-barrier, purged + embargoed)
  → TRAINING (deterministic seeds, walk-forward)
  → BACKTEST (friction-aware: spread / slippage / latency)
  → WALK-FORWARD VALIDATION
  → OOS GATE (hard: failure ⇒ REJECTED)
  → ROBUSTNESS STRESS
  → COUNTERFACTUAL / STRATIFICATION EVIDENCE
  → CANDIDATE REGISTRY (content-addressed)
  → SHADOW COMPARISON (zero order authority)
  → OPERATOR-GATED PROMOTION
```

## Key guarantees

- **Leakage prevention (INV-008):** purged + embargoed walk-forward
  (Lopez de Prado); broker history REPLACE+ALIGN; liquidity features strictly
  causal (confirmation bars, completed HTF buckets only). Purge/embargo
  defaults are wired into the production research path (BUG-183 regression
  suite).
- **Replay parity:** the replay feature vector is bit-exact vs the dataset
  (anti-leakage tests); live = replay = training semantics.
- **Determinism:** seeded training, frozen datasets, fingerprinted artifacts
  (dataset ID, schema hash, git commit in manifests and run snapshots).
- **Honest provenance:** run snapshots record effective purge/embargo and
  provenance fields — `NOT_RECORDED` is written when truthfully unknown, never
  backfilled.

## Components

| Component | Module | Note |
| :--- | :--- | :--- |
| Dataset builder + quality gates | `research/dataset.py`, `model_generation/` | fingerprinted, versioned |
| Backtest engine | `research/backtest.py` | deterministic, friction-aware |
| Walk-forward | `research/walkforward.py`, `training/walk_forward_trainer.py` | purged/embargoed |
| OOS gate | `research/oos.py` | macro-F1 / balanced-accuracy / ECE floors |
| Streaming replay | `research/streaming_replay.py` | logical clock, zero sleeps; simulated fills on historical bid/ask; **no `order_send` (test-enforced)** |
| Forward tests | `research/forward_test.py` | freeze capture at cutoff (model/scaler/strategy fingerprints), strict `timestamp > cutoff` streaming |
| Tick datasets | `research/mt5_tick_dataset.py` | canonical adapter surface, offline after acquisition |
| Counterfactual engine | `research/counterfactual.py` | walks NO_TRADE decisions on canonical ticks (CHG-0041) |
| Evidence + observability | `research/evidence.py`, `research/observability.py` | gate model, events, evidence vault, worker health |

## The 70D case study

The 70D series (`scalp_v3`: Base 50 + News 10 + Liquidity 10) is the reference
example of the stack working as designed: full parity/validation infrastructure
built, fair A/B/C benchmark run on real data — and the candidate **rejected**
(OOS NOT_ELIGIBLE). The live contract stayed 50D. Details:
[Status](../project/status.md) · internal reports under `docs/70D_*.md`.

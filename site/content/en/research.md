---
title: Research
description: How historical data becomes falsifiable evidence — datasets, backtests, replay, counterfactuals.
lang: en
---

Research exists to produce **falsifiable evidence**. A candidate that cannot
be falsified (no OOS, no replay parity, no provenance) never touches the live
path.

## The chain

```text
DATA (fingerprinted datasets) → FEATURES (causal contract) → LABELING (triple-barrier)
→ TRAINING (seeded, deterministic) → BACKTEST (friction-aware)
→ WALK-FORWARD (purged + embargoed) → OOS GATE (failure ⇒ REJECTED)
→ ROBUSTNESS STRESS → COUNTERFACTUAL → REGISTRY → SHADOW → OPERATOR PROMOTION
```

## Components

- **Datasets** — immutable, fingerprinted, provenance-tracked; tick datasets
  acquired through the certified adapter surface, offline after acquisition.
- **Backtests** — deterministic, spread/slippage/latency modeled.
- **Replay** — bit-exact vs dataset (anti-leakage tests); streaming replay runs
  the shared engine over a logical clock with simulated fills and is
  test-enforced to never call `order_send`.
- **Counterfactuals (CHG-0041)** — walks NO_TRADE decisions with hypothetical
  fills: 2095 decisions, 476 covered; CONFIDENCE_GATE stratum proved a valid
  filter (meanR −0.506); SUPPORT-margin stratum flagged for policy review.

## Provenance & determinism

Every run records dataset ID, schema hash, git commit, effective
purge/embargo (BUG-183 regression). `NOT_RECORDED` is written when honestly
unknown — never backfilled. Frozen-capture forward tests re-verify their
freeze after the run.

Deep dive: [methodology.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/methodology.md)
· [datasets.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/datasets.md)
· [replay.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/replay.md)
· [counterfactuals.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/counterfactuals.md).

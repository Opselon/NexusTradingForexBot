---
title: Backtesting
description: Deterministic, friction-aware backtests — what is simulated, what is honest about it.
lang: en
---

# Backtesting

## Determinism

Backtests (`research/backtest.py`) are deterministic: fixed seeds, frozen
datasets, no wall-clock dependence. Same inputs ⇒ same results.

## Friction model

Spread, slippage and latency are modeled, not ignored:

- fills on historical bid/ask (direction-aware),
- tick-level SL/TP first-touch resolution,
- logical latency applied to the decision path,
- robustness stress re-runs under degraded friction (spread/slippage/latency
  shocks) — a strategy that only works under ideal fills does not pass.

## Boundary honesty

- Backtests produce evidence, not promises. Results feed the validation
  pipeline; they never bypass the OOS gate.
- Fees/commissions follow the configured friction profile; metrics without the
  underlying evidence render `n/a` — never fabricated zeros.
- Streaming replay (`research/streaming_replay.py`) is the high-fidelity
  sibling: it runs the **shared engine** over historical events with a logical
  clock (zero sleeps) and simulated fills — and is test-enforced to never call
  `order_send`.

## Reading a backtest report

Reports include funnel (signals → orders → fills → exits), per-regime and
per-confidence stratification, MFE/MAE, and exit-mechanism attribution. See
[Validation](validation.md) for how these numbers are judged.

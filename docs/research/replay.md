---
title: Replay
description: Replay and streaming replay — bit-exact parity with the dataset, the anti-leakage guarantee, and how it differs from backtesting.
lang: en
---

# Replay

## What replay is

Replay re-runs the engine's decision logic over historical data and proves it
produces **the same decisions** the live/training pipeline produced on that
data. It is the execution-fidelity check between research and runtime.

## Two engines

| Engine | Module | Character |
| :--- | :--- | :--- |
| Dataset replay | `model_generation/replay.py` + `replay_70d_vector` | **bit-exact**: replay vector must equal the dataset's stored vector (anti-leakage tests) |
| Streaming replay | `research/streaming_replay.py` (CHG-0035) | shared `LiveEngine` over a logical clock (zero sleeps): incremental bar aggregation, causal 50D + news + liquidity at time T, frozen policy + RiskEngine, direction-aware simulated fills on historical bid/ask, tick SL/TP first-touch, ledger MFE/MAE — **NO `order_send` (test-enforced)** |

## Anti-leakage

Features at time T may consume only information available at T: liquidity
confirmation bars, completed HTF buckets, REPLACE+ALIGN history handling. The
parity tests fail loudly if a replay vector diverges from its dataset twin.

## Replay vs backtest

A backtest scores a strategy on history. A replay proves the **same code path**
that would run live behaves identically on history. Backtest answers "how
well?"; replay answers "is this actually the same system?".

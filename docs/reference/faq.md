---
title: FAQ
description: Honest answers to the questions newcomers actually ask.
lang: en
---

# FAQ

**What is Nexus?**
A research-driven quantitative trading platform: a hexagonal, event-driven
scalping engine for MetaTrader 5 (primary: XAUUSD M1) with causal features,
deep models, an invariant risk engine, deterministic research tooling and
forensic observability. See [Vision](../project/vision.md).

**Is this a live trading bot?**
It *can* execute live trades on MT5 — but PAPER is the default mode, SHADOW
carries zero order authority, and LIVE requires explicit interactive
confirmation. The repository's own evidence shows rejected candidates; this is
a research platform with a runtime, not a money printer.

**Can I run it without MT5?**
Yes — PAPER mode and the full test suite run without a broker. Docker works
out of the box in PAPER mode. SHADOW and LIVE need an MT5 terminal (Windows
x64).

**What is 70D?**
The canonical research feature contract: 50 base dimensions + 10 news + 10
liquidity. It is *not* live — the live contract is 50D (`scalp_v1`). The 70D
candidate is rejected so far on OOS evidence. See
[Status](../project/status.md).

**What is the difference between research and runtime?**
Research produces falsifiable evidence about candidates (datasets →
walk-forward → OOS → shadow). Runtime executes decisions with hard clamps.
Research has zero order authority — the two never mix.

**How is leakage (lookahead) prevented?**
Purged + embargoed walk-forward, strictly causal features (confirmation bars,
completed HTF buckets), REPLACE+ALIGN history handling, replay bit-exactness
tests, and recorded effective purge/embargo per run (BUG-183). See
[Walk-Forward](../research/walk-forward.md).

**How are models identified?**
Artifact-first manifests: dataset ID, feature-schema hash, scaler identity,
git commit — validated by the 10-gate load gate at every attach. A model
without provenance cannot attach.

**How does replay differ from backtesting?**
Backtest: score a strategy on history. Replay: prove the *same code path* as
live behaves identically on history (bit-exact vs dataset). See
[Replay](../research/replay.md).

**How is a feature contract enforced?**
Schema registry + SHA-256 hash over canonical feature JSON + inference
validator (dimension, bounds, finite, hash, scaler-dim match). Violations are
loud rejections, never silent padding.

**What is certified?**
See the [Capability Matrix](../project/capabilities.md) — each row names its
evidence. Certified ≈ formal forensic acceptance with reproducible artifacts
(e.g. release verification, provider-gate live smoke, OOS gate behavior).

**Is it profitable?**
No claim is made — that is the point. Published evidence includes negative
results (70D OOS rejection, counterfactual stratification). Judge from the
evidence, not from promises.

**Why is the bug ledger public?**
Because `agents/bugs.md` (root causes, evidence, regression guards) *is* the
engineering memory. Hiding it would destroy the property that makes the
platform trustworthy.

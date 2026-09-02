---
title: Nexus Scalp Engine
description: Research-driven quantitative trading & execution platform — causal features, deep models, invariant risk, deterministic research, forensic observability.
lang: en
---

:::cards
- **Evidence before claims** — metrics without evidence render `n/a`; negative results are published, not hidden.
- **No lookahead** — purged + embargoed walk-forward, strictly causal features, replay bit-exactness.
- **Runtime truth** — broker facts and runtime gates outrank intent and stale caches.
- **Zero order authority** — research, shadow and learning components physically cannot place orders.
:::

## What is Nexus?

A hexagonal, event-driven scalping engine for MetaTrader 5 (primary: XAUUSD M1)
that connects **causal 50D features**, **deep model inference** (dual-path
TCN + self-attention), an **SMC policy matrix**, a **bounded risk engine**,
**deterministic research tooling** (walk-forward, OOS gate, replay,
counterfactuals) and **forensic observability** into one auditable pipeline.

The platform publishes its own negative results — the heavily-engineered 70D
candidate was rejected by the out-of-sample gate, and that rejection is a
first-class result. A validation layer that can say *no* is the entire point.

## What you can do from here

- **Run it** — PAPER mode by default, never LIVE silently: see the
  [Quickstart](/getting-started/quickstart/).
- **Understand it** — [Architecture](/architecture/overview/) and the
  tick-to-decision [data flow](/architecture/data-flow/).
- **Judge the research** — [methodology](/research/methodology/) and the
  [project status](/project/status/), graded per capability with evidence.
- **Contribute** — the [contribution guide](/contributing/contribution-guide/)
  distills a multi-agent engineering contract into human workflow.

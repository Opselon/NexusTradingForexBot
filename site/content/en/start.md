---
title: Start here
description: What Nexus Scalp Engine is, why it exists, and how data becomes an auditable trading decision.
lang: en
---

Nexus Scalp Engine is a **research-driven quantitative trading platform**: a
hexagonal, event-driven scalping runtime for MetaTrader 5 (primary market
XAUUSD M1) that connects market data, causal feature engineering, model
inference, strategy policy, risk control, replay, execution, observability and
reproducible validation into one auditable pipeline.

<div class="callout"><p><strong>Not a profit promise.</strong> This is a research
and engineering platform. Statuses are evidence-graded and negative results are
published — including the rejection of our own flagship 70D research candidate
on out-of-sample evidence. Leveraged scalping carries extreme financial risk.</p></div>

## What makes it different

- **Evidence before claims** — metrics without evidence render `n/a`, never fake zeros.
- **No lookahead** — purged + embargoed walk-forward, strictly causal features (INV-008).
- **Causal parity** — live = replay = training semantics, protected by schema hashing.
- **Runtime truth** — broker truth wins over stale state; gates are authorities (INV-011).
- **Zero order authority** for research/learning components (INV-002); the tick hot path never blocks (INV-001).
- **Validation before promotion** — OOS failure ⇒ REJECTED; promotion strictly operator-gated.

## The pipeline at a glance

```text
Market Data → Causal Features (50D live / 70D research) → Inference Validator
→ ScalpNet → Regime → SMC Policy → Risk Engine → OrderManager
→ Broker (MT5 / paper / shadow) → Accounting (immutable ledger)
→ Experience & Research loop → Operator-gated promotion
```

## Explore

<div class="grid">
  <div class="card"><h3>🚀 Run it</h3><p>PAPER default · SHADOW zero-order · LIVE gated.</p><a href="contributing.html#run">Quickstart →</a></div>
  <div class="card"><h3>🗺️ Architecture</h3><p>Hexagonal layers, data flow, model pipeline.</p><a href="architecture.html">Open →</a></div>
  <div class="card"><h3>🔬 Research</h3><p>Walk-forward, OOS gate, replay, counterfactuals.</p><a href="research.html">Open →</a></div>
  <div class="card"><h3>📌 Status</h3><p>Certified vs experimental vs planned — evidence-graded.</p><a href="status.html">Open →</a></div>
  <div class="card"><h3>🗓️ Roadmap</h3><p>NOW / NEXT / LATER with completion gates.</p><a href="roadmap.html">Open →</a></div>
  <div class="card"><h3>🧭 FAQ & Glossary</h3><p>Honest answers, project vocabulary.</p><a href="reference.html">Open →</a></div>
</div>

## Run it in 60 seconds

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -e .[dev]
nexus doctor          # read-only diagnostics
nexus start           # PAPER mode (safe default) → http://127.0.0.1:8080
```

End users can download the packaged Windows release (no Python needed) from
GitHub Releases, or use the PowerShell bootstrap installer. Full instructions:
[Installation](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/getting-started/installation.md).

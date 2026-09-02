---
title: Reference & FAQ
description: Project glossary, terminology and honest answers to the questions newcomers ask.
lang: en
---

## FAQ

**What is Nexus?**
A research-driven quantitative trading platform: hexagonal, event-driven
scalping engine for MetaTrader 5 (XAUUSD M1) with causal features, deep
models, invariant risk, deterministic research and forensic observability.

**Is this a live trading bot?**
It *can* execute live trades — but PAPER is the default, SHADOW carries zero
order authority, LIVE requires explicit interactive confirmation, and the
repository publishes rejected candidates. It is a research platform with a
runtime, not a money printer.

**Can I run it without MT5?**
Yes — PAPER mode and the full test suite run without a broker; Docker works
out of the box in PAPER mode.

**What is 70D?**
The canonical research feature contract (50 base + 10 news + 10 liquidity).
Not live — the live contract is 50D. The 70D candidate is rejected so far on
OOS evidence.

**How is leakage prevented?**
Purged + embargoed walk-forward, strictly causal features, REPLACE+ALIGN
history handling, bit-exact replay tests, recorded effective purge/embargo
per run.

**How are models identified?**
Artifact manifests: dataset ID, feature-schema hash, scaler identity, git
commit — validated by the 10-gate load gate at every attach.

**How does replay differ from backtesting?**
Backtest scores a strategy on history; replay proves the *same code path* as
live behaves identically on history (bit-exact vs dataset).

**Is it profitable?**
No claim is made — that is the point. Judge from the published evidence,
including the negative results.

## Glossary (selected)

| Term | Meaning |
| :--- | :--- |
| 50D / scalp_v1 | the ACTIVE live 50-dimensional causal feature contract |
| 70D / scalp_v3 | canonical research contract: Base 0..49 + News 50..59 + Liquidity 60..69 |
| Schema hash | SHA-256 over canonical feature JSON — reordering invalidates models |
| Shadow | live-data runtime, `simulated=True`, zero order authority |
| Replay | re-running engine logic on history; must be bit-exact vs dataset |
| OOS gate | hard out-of-sample gate; failure ⇒ REJECTED |
| Champion / Challenger | production model vs candidate; promotion operator-gated |
| Deploy gate | forensic pre-release verdict: PASS / REVIEW / BLOCK |
| Provenance | identity chain of an artifact; `NOT_RECORDED` when honestly unknown |
| INV-NNN / BUG-NNN / CHG-NNNN | runtime invariants / forensic bug ledger / change registry |

Full glossary: [glossary.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/reference/glossary.md)
· FAQ: [faq.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/reference/faq.md).

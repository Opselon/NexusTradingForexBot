---
title: Glossary
description: Project-specific vocabulary of the Nexus Scalp Engine — exact meanings, not generic programming definitions.
lang: en
---

# Glossary

Project-specific terms. Product/module names (Nexus, ScalpNet, OrderManager,
MT5, 70D, 50D) are canonical and never translated in code; each language's
[terminology file](terminology.md) defines how they are presented.

| Term | Meaning in NSE |
| :--- | :--- |
| **50D / scalp_v1** | the 50-dimensional causal base feature contract — the ACTIVE live feature schema |
| **70D / scalp_v3** | the canonical research feature contract: Base 50 (0..49) + News 10 (50..59) + Liquidity 10 (60..69); candidate-only, not live |
| **scalp_v4** | a candidate 70D-family contract (integration-era); classified LEGACY/semantically-different vs v3 |
| **Schema hash** | SHA-256 over canonical JSON of feature (index, name, family) — reordering invalidates models |
| **ScalpNet** | dual-path model: 2D MLP for single-tick snapshots, 3D TCN + self-attention for sequences; 4-logit head |
| **Champion / Challenger** | production model vs candidate; promotion is operator-gated, never automatic |
| **Load gate (10-gate)** | manifest/hash/scaler/dimension/family validation every model bundle passes before attach |
| **Shadow** | live-data runtime with `simulated=True` and **zero order authority** |
| **Replay** | re-running engine logic on history; must be bit-exact vs the dataset (anti-leakage) |
| **Streaming replay** | shared LiveEngine over a logical clock with simulated fills; test-enforced no `order_send` |
| **Forward test** | frozen-capture experiment: fingerprints frozen at cutoff, streams only `timestamp > cutoff`, re-verifies after |
| **Counterfactual** | walking NO_TRADE decisions with hypothetical fills to score abstentions (CHG-0041) |
| **Walk-forward** | purged + embargoed temporal validation (Lopez de Prado) |
| **OOS gate** | hard out-of-sample gate; failure ⇒ REJECTED |
| **Purge / Embargo** | gaps that prevent label/serial leakage across folds (defaults 300s / 60s — BUG-183) |
| **Regime Guardian** | regime classifier gating policy behavior (e.g. flapping regimes) |
| **SMC** | Smart Money Concepts policy matrix: Order Blocks, Fair Value Gaps, liquidity sweeps (~30 rules) |
| **Provenance** | the identity chain of an artifact: dataset ID, schema hash, git commit, config — `NOT_RECORDED` when honestly unknown |
| **Runtime truth** | the principle that live runtime state (gates, broker facts) is authoritative over intent/stale caches |
| **Order authority** | the exclusive ability to place/modify/close orders — held only by OrderManager through ports |
| **Deploy gate** | forensic pre-release verdict: PASS / ALLOW_WITH_WARNING / REVIEW / BLOCK |
| **Feature contract** | the schema+hash+bounds+ordering agreement binding features across training/replay/live |
| **Certification** | evidence-graded status: formal forensic acceptance with reproducible artifacts |
| **Experience ledger** | immutable record of outcomes feeding autopsy/behavior intelligence |
| **MANAGED_LOSS** | an exit classified as strategy-managed loss — distinct from a broken strategy |
| **Provider gate** | bounded LLM/AI-service access layer: rate limits, circuit breaker, auto-disable (CHG-0034) |
| **INV-NNN** | runtime invariant (e.g. INV-001 no sync DB on tick path) — see `agents/runtime_invariants.md` |
| **BUG-NNN** | forensic bug ledger entry with root cause + evidence + regression guard |
| **CHG-NNNN** | registered change (change-control lifecycle) |
| **NO_TRADE** | model's abstain class (0); WAIT (3) is the deferral class |

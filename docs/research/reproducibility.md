---
title: Reproducibility
description: How NSE makes research runs reproducible — fingerprints, frozen captures, deterministic seeds, provenance snapshots.
lang: en
---

# Reproducibility

## Identity chain

Every research artifact carries the chain that produced it:

```text
dataset fingerprint → feature schema hash → model manifest (scaler, weights)
→ git commit → effective config (seeds, splits, purge/embargo)
```

Run snapshots (`research_run_snapshots`) store these at run time; provenance
fields may be `NOT_RECORDED` (honest) but are never invented or backfilled.

## Determinism sources

- seeded training (seed-before-model construction — BUG-101 regression),
- frozen datasets (immutable, fingerprinted),
- logical-clock streaming replay (zero sleeps),
- freeze capture at forward-test cutoff + **post-run re-verification** of the
  freeze (`research/forward_test.py`).

## What breaks reproducibility (and is therefore guarded)

| Threat | Guard |
| :--- | :--- |
| dataset mutation | immutable fingerprinted artifacts |
| feature reordering | schema hash changes → model invalidates loudly |
| silent default drift | effective config recorded per run (BUG-183) |
| RNG divergence | seed-before-model construction |
| "works on my machine" | manifests carry git commit + dataset ID |

## Rebuilding from ledgers

The accounting/experience ledgers are immutable; research evidence stores are
append-only. The system is designed so that **everything derivable is
rebuildable** from the immutable records.

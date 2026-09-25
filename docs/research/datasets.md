---
title: Datasets
description: Canonical dataset contracts — fingerprints, quality gates, provenance, and the tick-dataset cache.
lang: en
---

# Datasets

## Bar datasets

Built by `research/dataset.py` + `model_generation/` with quality gates
(coverage, ordering, duplicates, horizon boundaries). Each dataset is a
versioned artifact with a fingerprint (`ds_*` id) used in manifests and run
snapshots.

## Tick datasets (CHG-0041 / TICK_DATASET_META v2)

`research/mt5_tick_dataset.py` acquires ticks from the **certified adapter
surface** (`copy_ticks_range`, `COPY_TICKS_ALL`), with:

- bounded, deduplicated acquisition (post BUG-188 semantics),
- immutable parquet store with **content fingerprints**,
- provenance metadata v2 (symbol, window, source, acquisition identity),
- cache-hit idempotence — re-running acquisition never duplicates.

After acquisition the dataset is fully **offline**: research needs no broker.

## Rules

1. A dataset is immutable once fingerprinted — corrections produce a new
   dataset, never a mutation.
2. Every experiment names its dataset ID; manifests and run snapshots carry it.
3. Dataset-fairness: A/B/C model comparisons must consume equal data windows,
   seeds and splits (the TASK-04 protocol).
4. Provenance fields may be `NOT_RECORDED` (honest) — they are never invented.

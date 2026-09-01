---
title: Out-of-Sample Gate
description: The hard OOS gate — floors, rejection semantics, and the evidence that it actually rejects.
lang: en
---

# Out-of-Sample (OOS) Gate

## Contract

`research/oos.py` is a **hard gate**: out-of-sample failure ⇒ candidate
REJECTED, regardless of in-sample performance. There is no override path in
the pipeline.

## Floors (model lifecycle validation)

| Metric | Floor |
| :--- | :--- |
| macro-F1 (OOS) | ≥ 0.34 |
| balanced accuracy | ≥ 0.34 |
| calibration error (ECE) | ≤ 0.15 |
| minimum evidence | ≥ 100 rows |

## Evidence the gate bites

The 70D liquidity candidate — the most heavily engineered research series in
the repository — was rejected by this exact gate (OOS NOT_ELIGIBLE) after
real-data walk-forward and shadow benchmarks came back negative/inconclusive.
A gate that rejects nothing is decoration; this one has a public rejection on
its record. See [Project Status](../project/status.md) and
`docs/TASK-05-70D-SHADOW-FINAL.md`.

## Temporal OOS protocol

Temporal splits only (`timestamp`-ordered); the last period is held out;
freeze/re-verify is used for forward tests (`research/forward_test.py`):
capture frozen fingerprints at cutoff, stream only `timestamp > cutoff`, then
re-verify the freeze after the run.

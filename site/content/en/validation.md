---
title: Validation
description: The gate chain — what "validated" means and why rejections are published.
lang: en
---

Validation is the reason the platform exists. A failure at any gate is
terminal for that candidate — no averaging, no overrides.

## The gate chain

| Layer | Gates |
| :--- | :--- |
| Dataset | quality gates · fingerprinting · provenance |
| Features | schema hash · dimension · bounds · ordering |
| Model | 10-gate load gate · calibration · min-evidence |
| Research | purged/embargoed walk-forward · OOS floors · robustness |
| Governance | 14-gate verification · promotion transaction |
| Runtime | forensic deploy gate before any release |
| Release | SHA-256 · manifests · SBOM · post-publish verification |

## OOS floors (model lifecycle)

macro-F1 ≥ 0.34 · balanced accuracy ≥ 0.34 · ECE ≤ 0.15 · evidence ≥ 100 rows.

## The gate bites — public proof

The 70D liquidity candidate — the most heavily engineered research series in
the repository — was rejected by this exact chain (OOS NOT_ELIGIBLE) after
real-data walk-forward and shadow benchmarks came back negative. The live
contract stayed 50D. A gate that rejects nothing is decoration; this one has a
public rejection on its record.

Deep dive: [out-of-sample.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/out-of-sample.md)
· [validation.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/validation.md)
· [reproducibility.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/reproducibility.md).

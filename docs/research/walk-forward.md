---
title: Walk-Forward
description: Purged and embargoed walk-forward validation — why the purge exists and what happens when it was missing.
lang: en
---

# Walk-Forward

## Method

`training/walk_forward_trainer.py` + `research/walkforward.py` implement
**purged, embargoed walk-forward** (Lopez de Prado): training folds are
separated from test folds by a purge gap (label horizon) and an embargo gap
(serial-correlation buffer), evaluated across temporally ordered folds.

## Defaults that matter (BUG-183)

The production research path once ran with purge/embargo silently disabled —
the constants existed but weren't wired. This is recorded as BUG-183 and fixed
with regression tests: `DEFAULT_PURGE_SECONDS = 300`, `DEFAULT_EMBARGO_SECONDS
= 60` are now wired into `ResearchPipeline.validate_candidate`,
`OOSGate.evaluate`, `WalkForwardEngine.validate` and `BacktestEngine.run`, and
the **effective** purge/embargo values are recorded in every run's config.

## Why it is non-negotiable

A walk-forward without purge/embargo leaks label information across folds and
overstates performance — the classic quantitative backtest crime. The
repository treats a purge regression the same way it treats a risk-clamp
regression: critical.

## Validation gates

OOS floors (macro-F1, balanced accuracy ≥ 0.34; ECE ≤ 0.15; minimum evidence
100 rows) apply to walk-forward outputs. Failure ⇒ the candidate is REJECTED,
full stop. See [OOS gate](out-of-sample.md).

# src/nexus_scalp/research/oos.py

- PURPOSE: PHASE 09B hard out-of-sample gate (spec 15/34/38): a candidate can
  NOT become VALIDATED on in-sample excellence alone; negative OOS ⇒ REJECTED
  even at high win rate.
- ARCHITECTURE LAYER: Research (pure gate over splits; no order authority).
- RESPONSIBILITY: split the dataset temporally (train | validation | OOS),
  measure in-sample (train+validation) vs OOS expectancy under the same
  assumptions, enforce the expectancy floor and the relative-degradation
  ceiling, and produce an OOSResult with an explicit reason string.
- DEPENDENCIES: `research.metrics` (compute_backtest),
  `research.models` (OOSResult, ExecutionAssumptions, ResearchDataset),
  `research.splitting` (split_temporal), observability.logging.
- CONNECTS TO: pipeline stage OOS (hard gate); its status/reason feed
  scoring verdict rules and registry invariants; family-select validation
  runs it on the candidate's own family dataset.

- KEY CONCEPTS:
  - Floors: MIN_OOS_EXPECTANCY_R = 0.0 (line 25) and MAX_OOS_DEGRADATION =
    1.0 — a 100% relative drop from in-sample is the hard ceiling
    (line 27, "100% relative drop is the hard ceiling"; note the ceiling
    only bites when in_exp > 0, see pitfall).
  - `OOSGate.evaluate` (lines 43-120): split_temporal (val 0.2 / oos 0.2,
    optional purge/embargo seconds passed through from the pipeline);
    in-sample = train + validation (OOS never touches backtest training).
    Degradation = (in_exp − oos_exp)/|in_exp| when in_exp != 0.
  - Decision logic (lines 84-99): pass requires oos_exp >= floor; hard FAILs:
    no OOS samples; in_exp > 0 AND degradation > 1.0; informational reason
    when BOTH in-sample and OOS are non-positive. `status="PASS"` only when
    none of the conditions fail; default reason when passing:
    "OOS evidence confirms positive edge".
  - Logs `[OOS] event=RESULT` with expectancy + status.

- HOT PATH / PERFORMANCE: one split + two compute_backtest runs; worker-cycle
  only.

- EDGE CASES & PITFALLS:
  - With in_exp <= 0 (weak in-sample), the degradation ceiling is never
    evaluated — a strategy with negative in-sample but positive OOS can
    PASS if oos_exp >= 0.0; the reason list only adds the informational
    "both non-positive" message when both are <= 0. This is deliberate
    ("OOS evidence confirms edge") but means the gate is permissive for
    strategies whose in-sample is bad and OOS is merely non-negative.
  - `oos_win_rate` is reported but never gated — the gate is expectancy-only.
  - The split is re-derived here rather than reusing the pipeline's backtest
    split: use_split backtest (train+val) and this in_sample (train+val
    recomputed) are the same construction but separate code paths — purge/
    embargo parameters must be passed identically by callers or the two can
    disagree.
  - Degradation uses |in_exp| denominator; tiny in_exp inflates degradation
    without bound (ceiling 1.0 catches it only when in_exp > 0).
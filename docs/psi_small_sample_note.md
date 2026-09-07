# PSI small-sample behavior of the canonical drift monitor (evidence note)

Mission 7A requires drift detection that does NOT trigger on tiny samples.
While wiring `risk/drift_breaker.py` to the canonical
`shadow70.Shadow70DriftMonitor`, we measured the PSI noise floor of the
monitor's own estimator rather than trusting the thresholds blindly.

## Method

Identical-distribution experiment: draw `n` i.i.d. samples from
N(0, 0.5), compute `_psi(live, _normal_reference(0.0, 0.5, n))` (exactly
what `Shadow70DriftMonitor.evaluate()` computes), repeat over 150-300
seeds, record quantiles. Any nonzero PSI here is pure sampling noise —
the live window and the reference are the SAME distribution.

## Measured noise floor (2026-09-07, repo venv)

| n    | PSI p50 | p90   | p99   |
|------|---------|-------|-------|
| 30   | 0.394   | 0.890 | 1.415 |
| 50   | 0.412   | 0.728 | 1.044 |
| 100  | 0.166   | 0.353 | 0.587 |
| 200  | 0.108   | 0.228 | 0.338 |
| 400  | 0.023   | 0.053 | 0.085 |
| 800  | 0.013   | 0.026 | 0.038 |

Reference thresholds (health.py): WATCH 0.10 / WARNING 0.20 / CRITICAL 0.30.
Sample floor `DRIFT_MIN_SAMPLES = 30`.

## Consequences (binding for consumers)

1. At the canonical 30-sample floor, the p50 identical-distribution PSI
   (~0.39) already EXCEEDS the CRITICAL threshold: at n≈30 the PSI
   metric is dominated by binning noise and its alerts are
   statistically meaningless. This is a property of the estimator at
   small n, not of the data.
2. A drift state is only trustworthy at n >= ~400 (p90 = 0.053 < WATCH),
   or when alerts are corroborated by the mean-shift / std-ratio /
   missing-rate metrics (those estimators have much smaller noise
   floors: mean-shift 0.15/0.30/0.50 in normalized units).
3. `FeatureDriftBreaker.state()` therefore reports the monitor's
   INSUFFICIENT_EVIDENCE state below the floor, and consumers should
   treat a lone CRITICAL PSI alert under ~400 samples with skepticism —
   mission 7A's "do not trigger on tiny sample sizes" rule.
4. Thresholds were NOT changed (no green-theater tuning); the monitor's
   documented contract stands. This note records the operating envelope.

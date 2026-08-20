# src/nexus_scalp/shadow/shadow70/health.py

- PURPOSE: 70D feature health + drift monitoring (TASK-05-70D-SHADOW
  spec 20/21/22): per-liquidity-feature statistics over a bounded window
  (finite/missing/stale/zero rates, mean, std, min, max) plus PSI and
  mean/std/missing-rate drift vs the training distribution, classified
  NORMAL/WATCH/WARNING/CRITICAL. PURE: no I/O, no DB, no torch; bounded
  memory (only the last `window` vectors retained). Drift is
  OBSERVATIONAL — never changes trading (INV-018).
- ARCHITECTURE LAYER: Domain (monitoring, pure computation).
- RESPONSIBILITY: Shadow70FeatureHealthMonitor (window + per-feature
  stats), Shadow70DriftMonitor (PSI/mean/std/missing drift → alerts),
  dataclasses Shadow70FeatureHealth / Shadow70DriftAlert, helpers
  _mean_std/_psi/_normal_reference.
- DEPENDENCIES: models (LIQUIDITY_FEATURE_NAMES, LIQUIDITY_SLICE),
  math, random, dataclasses, logging.
- CONNECTS TO: runtime (feeds vector70 via update), store
  (save_feature_health / save_drift_alerts through the worker), summary
  endpoint for UI.
- KEY CONCEPTS:
  - Windows: health monitor window default 1000 (stale-marked); drift
    monitor buffer capped at 5000.
  - _psi: Population Stability Index over [-3,3], 10 bins, edges
    ±inf at the extremes, eps=1e-6 smoothing; STDLIB ONLY (numpy-free).
  - _normal_reference: deterministic reference N(mean, std) sample via
    random.Random(1234), clipped [-3,3], quantile-spaced by construction;
    the PSI reference is a NORMAL PDF from training mean/std — NOT a
    3-point spike (previous degenerate stand-in produced inflated PSI
    for zero-variance live windows — documented fix).
  - DriftMonitor.thresholds: PSI watch/warn/crit = 0.10/0.20/0.30; mean
    shift = 0.15/0.30/0.50; std ratio = 1.30/1.60/2.00 (live/reference);
    missing-rate delta = 0.05/0.10/0.20. Severity via _severity
    (strictly greater than threshold). Drift requires min 30 buffer
    samples AND >= 5 finite values per feature AND a reference (else
    INSUFFICIENT_EVIDENCE / nothing).
  - evaluate() emits up to 4 alert kinds per feature (PSI,
    MEAN_SHIFT, STD_RATIO, MISSING_RATE_DELTA); summary(): honest
    NO_REFERENCE_DISTRIBUTION / INSUFFICIENT_EVIDENCE / EVALUATED with
    the worst severity + last 50 alerts.
  - FeatureHealthMonitor.update: extracts the liquidity slice ONLY
    (indices 60..69); wrong slice length → False; stale marks track
    stale observations for stale_rate.
- HOT PATH / PERFORMANCE: update() is an O(10) append — safe on the tick
  path; evaluate() is O(window*10) and runs on a cadence off-path.
- EDGE CASES & PITFALLS: zero-variance live windows — ref_std floored at
  1e-6 and std ratio blows up → STD_RATIO CRITICAL for a genuinely dead
  feature (overlaps the forensics FEATURE_DEAD classification — two
  monitors, different vocabularies); missing_rates computed from buffer
  length (non-finite = missing); if < 5 finite values the feature is
  skipped entirely (no alert at all — silent for sparse features); the
  drift alerts use uuid-free deterministic alert creation — store builds
  alert_id deterministically from feature+metric+samples (see
  shadow70/store.py) so repeated identical alerts collide by design
  (INSERT OR IGNORE).
# src/nexus_scalp/governance/evidence.py

- PURPOSE: Eventual outcome linkage + live calibration + drift
  (TASK-6 spec 16-20). Shadow predictions at T0 are linked to outcomes
  only AFTER T0+horizon (no future info at prediction time); drift
  creates ALERTS only, never auto-retrain/promotion; append-only evidence
  used by review, never as a live training label (spec 19).
- ARCHITECTURE LAYER: Domain (governance evidence).
- RESPONSIBILITY: outcome_for_decision, calibration_buckets / brier_score
  / ece_score, detect_drift, backtest_live_divergence.
- DEPENDENCIES: governance.models (CalibrationBucket, DriftAlert),
  sqlite3, uuid, logging.
- CONNECTS TO: review/reporting, governance store events, shadow outcome
  linkage worker, forensics experience-gap (LINKED rows source).
- KEY CONCEPTS:
  - outcome_for_decision resolution order: (1) REAL trade —
    audit_experience_outcomes row by decision_id (canonical realized R,
    authoritative); (2) SHADOW path — price_path last close vs entry with
    1R = 0.1% of entry (triple-barrier proxy), R per action sign
    (BUY=+mvt/entry*0.001, SELL=-). Returns linkage_state
    LINKED/DEFERRED (horizon not reached: price path < 2)/NO_PATH (no
    entry)/UNRESOLVED (empty/unparsable).
  - Default horizon 15 bars (M1 decisions → 15 min, matching
    triple-barrier max_holding).
  - calibration_buckets: exact half-open [lo,hi) buckets, width 0.1,
    index = min(9, floor(c*10)) — 0.6 belongs ONLY to 0.6-0.7; empty
    buckets skipped; accuracy + mean_confidence per bucket.
  - brier_score: mean squared (p - y) over 0/1 correctness; empty → 0.0.
  - ece_score: Σ (n_bucket/total * |acc - mean_conf|), 0.0 when empty.
  - detect_drift, per kind (min 30 samples each):
    - PROBABILITY: per-class mean vs reference (default 0.80/0.10/0.10/
      0.00), max abs shift > 0.20 → alert; severity CRITICAL if > 2x.
    - ACTION: NO_TRADE frequency shift vs 0.80 reference, threshold 0.25.
    - FEATURE: mean |z| over the ACTUAL vector width (TASK-14 hardening:
      uses len(window[0]), NOT min(50) — the old code silently truncated
      70D tails; shorter vectors safe via per-index guard),
      threshold 2.0.
    - NEWS: mean vector magnitude > 0.5.
  - backtest_live_divergence: flags BACKTEST_LIVE_DIVERGENCE when live
    accuracy/expectancy is > tolerance (0.10R) worse than backtest, only
    with >= 30 live samples; never retunes.
- HOT PATH / PERFORMANCE: called off-tick (periodic worker);
  feature mean accumulation is O(window*width).
- EDGE CASES & PITFALLS: DEFERRED rows are not failures — horizon not yet
  reached; real-trade lookup swallows DB errors (falls back to shadow
  path silently); DriftAlert ids are uuid-based (not deterministic);
  drift with no window never emits; per-kind windows are independent
  (a single window can trigger several alerts).
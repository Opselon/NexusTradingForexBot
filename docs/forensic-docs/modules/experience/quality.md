# src/nexus_scalp/experience/quality.py

- PURPOSE: Deterministic outcome decomposition and behavioral-flag analysis —
  answers "WHY did this trade work or fail?" separately from "did the account
  make money?". Every score and flag is a pure function of recorded evidence:
  no model inference, no randomness, no hidden state — always recomputable.
- ARCHITECTURE LAYER: Domain/quality attribution (pure computation over frozen
  domain models; no DB access — the analyzer is a stateless function object).
- RESPONSIBILITY: Enforce the Phase 08 separation (docstring lines 11-24):
  SIGNAL/STRATEGY quality (was the thesis right?), REGIME FIT, ENTRY, RISK,
  POSITION MANAGEMENT, EXIT, EXECUTION. A profitable trade with poor
  strategy/entry evidence is `profitable_for_wrong_reason`; a losing trade with
  sound decision and risk is `acceptable_loss` — "won = good" must never become
  the learning rule. All thresholds live in `DecompositionThresholds` so they
  are auditable/testable, not scattered magic numbers.
- DEPENDENCIES: `experience.models` (BehavioralFlag, ExecutionContext,
  ExperienceRecord, OutcomeDecomposition, PositionBehavior, QualityVerdict),
  observability.logging. Pure stdlib otherwise (dataclasses).
- CONNECTS TO: `intelligence.py` (analyze at outcome-record time),
  `intelligence/autopsy.py` (TradeAutopsyEngine wraps an OutcomeAnalyzer and
  packages its decomposition into a narrative), `evaluator.py` aggregates the
  decomposition's quality scores; thresholds are consumed by
  intelligence.py's reentry-window measurement.
- KEY CONCEPTS:
  - `DecompositionThresholds` (lines 47-79): frozen dataclass of 14 named
    thresholds: entry_chase_slippage_r 0.15, slippage_anomaly_r 0.30,
    premature_entry_mfe_r 0.15, invalidation_mae_r 0.90, confidence_overshoot
    0.75/‑0.50R, excessive_hold_factor 3.0, risk_deviation_tolerance 0.25,
    early_exit_mfe_r 1.20 + capture_floor 0.35, poor_stop_atr_multiple 0.50,
    default_min_rr 1.2, reentry_window_sec 300, reentry_count_threshold 3.
  - Helpers: `_clamp` → [-1,1]; `_verdict` (score ≥0.35 GOOD, ≥−0.15
    ACCEPTABLE, else POOR); `_safe_ratio` returns 0.0 on zero denominator
    (missing evidence yields neutral scores + UNKNOWN verdicts, never invented).
  - `OutcomeAnalyzer.analyze` (lines 127-217): computes planned_risk from the
    record; mae_r/mfe_r prefer already-normalized behavior fields, falling back
    to points/planned_risk; slippage_r = |slippage|/risk. Produces 8 scores,
    3 verdicts, the two flags (profitable_for_wrong_reason: realized_r>0 AND
    (strategy_quality<0 OR entry_quality<−0.25); acceptable_loss: realized_r≤0
    AND strategy_quality≥−0.35 AND risk_quality≥0 AND no RISK_DEVIATION /
    THESIS_INVALIDATION_IGNORED flag).
  - Dimension scores (all clamped to [-1,1]):
    - `_entry_quality` = favourable − adverse − 2×slippage_r (neutral 0.0 when
      no excursion evidence at all).
    - `_risk_quality`: stop width vs ATR (inside 0.50×ATR −0.6, ≤3×ATR +0.4,
      beyond 3×ATR −0.2) + R/R vs recorded policy floor (default 1.2).
    - `_execution_quality`: −1.0 on any rejection_reason; base 1−3×slippage_r,
      −0.3 if latency>1000ms, −0.15 if >500ms.
    - `_management_quality`: capture ratio (realized/MFE − 0.5)×2; +0.2 when
      sl_moved and realized≥0; +0.1 partial close and profit; never-in-profit
      deep-MAE-without-SL-move penalty −0.4.
    - `_exit_quality`: losing stop exit +0.2; else capture-based
      (capture−0.4)×1.8 with TAKE_PROFIT +0.2, RISK_FREE/GIVEBACK +0.1.
    - `_strategy_quality`: thesis_strength=min(1.5,MFE)/1.5 minus
      thesis_damage=min(1.5,MAE)/1.5; a win achieved after >0.90R MAE with
      MFE<0.5R is capped at −0.2 (luck, not edge).
    - `_signal_quality`: realised_edge − (confidence − 0.5), confidence from
      signal_confidence or model_probability.
    - `_regime_fit`: normalized MFE−MAE, ÷1.5 when ATR evidence exists.
  - Behavioral flags (`_behavioral_flags`, lines 363-436): 12 deterministic
    rules — slippage≥0.15R ENTRY_CHASE AND ≥0.30R EXECUTION_SLIPPAGE_ANOMALY;
    MFE≤0.15&MAE>0.15 PREMATURE_ENTRY; confidence≥0.75 & r≤−0.50
    CONFIDENCE_OVERSHOOT; MAE≥0.90R & loss & !sl_moved &
    !(SL/STOP mechanism) THESIS_INVALIDATION_IGNORED (a stop-out is the system
    RESPECTING the invalidation boundary — reserved for carried exits);
    duration>3×expected & loss EXCESSIVE_HOLD_DURATION; |actual−planned
    SL|/planned>0.25 RISK_DEVIATION; ≥3 recent same-family entries
    REENTRY_OVERTRADING; MFE≥1.2R & profit & capture<0.35 EARLY_EXIT;
    stop<0.5×ATR POOR_STOP_PLACEMENT; R/R<floor (policy or 1.2)
    WEAK_SETUP_ACCEPTED. Flags preserve declaration order, deduped.
  - `compute_behavior_metrics` (lines 439-479): builds PositionBehavior with
    risk-normalized mae_r/mfe_r and non-negative clamps on all durations;
    the standard converter for raw position data into the domain model.
- HOT PATH / PERFORMANCE: Pure arithmetic; runs once per closed outcome (and
  per autopsy). No I/O. Called from the outcome recorder and the worker.
- EDGE CASES & PITFALLS:
  - Missing evidence intentionally yields 0.0/UNKNOWN — consumers must not
    reinterpret neutral scores as endorsement (the pre-trade gate treats
    absence as INSUFFICIENT_EVIDENCE).
  - `_signal_quality` punishes high confidence when realized edge is small,
    and could return negative values for truthful-but-unconfirmed signals —
    by design, confidence overshoot is a measurable failure.
  - `_exit_quality` matches "SL"/"STOP" by SUBSTRING on uppercase reason —
    any reason string containing "SL" (e.g. "SL_MOVED_CONTEXT") is treated as a
    stop exit for losing trades; acceptable but brittle to taxonomy changes.
  - DELTA vs behavior.py: Phase 08 flags are per-record outcome flags; the
    intelligence package's BehaviorDetectionEngine derives its OWN patterns from
    canonical ledger rows — two parallel flag universes that must be kept in
    sync by versioning (behavior-v1 / anomaly-v1).
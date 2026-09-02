# src/nexus_scalp/intelligence/behavior.py

- PURPOSE: PHASE 09 measurable behavioral-pattern detection (TASK-2 upgraded) —
  derives objective patterns from RECORDED NUMBERS ONLY, plus the canonical
  analysis batch driver and anomaly detection. Never asserts "greed"/"fear";
  it derives PROFIT_GIVEBACK etc. from provable numbers.
- ARCHITECTURE LAYER: Application (derived intelligence; offline batch over
  `audit_ledger` canonical rows; persists detections/analysis/anomalies via
  the audit queue).
- RESPONSIBILITY (docstring lines 12-57): every detection carries
  behavior_id, evidence (threshold/actual/expected/explanation), severity,
  confidence, timestamp; detections are append-only; algorithm versioning
  (`behavior-v1` / `anomaly-v1`) keeps old analysis records reproducible when
  thresholds change; thresholds centralized at module level (task §13);
  derived records key on (ticket, behavior_version, anomaly_version) so
  re-running identical versions MUST NOT duplicate records.
- DEPENDENCIES: `audit_repository.AuditRepository`,
  `experience.models.ExperienceRecord`, `intelligence.models` (AnomalyEvent,
  BehaviorAnalysis, BehaviorDetection, BehaviorSeverity),
  `accounting.normalize.normalize_trade_row` (canonical trade normalization),
  stdlib (hashlib, json, statistics, uuid).
- CONNECTS TO: worker.py (`_refresh_behavior` → analyze_canonical_trades
  bounded 200 trades/cycle), store.py (reads), behavioral-detection UI, and
  `experience.ledger` for strategy hold baselines.
- KEY CONCEPTS:
  - Detector classes (module constants lines 110-141): HOLD (OVERHOLD_LOSER,
    EXCESSIVE_HOLD_TIME), EXIT (PROFIT_GIVEBACK, MISSED_BREAKEVEN,
    PREMATURE_BREAKEVEN, EXIT_CLASSIFICATION_ANOMALY), MODEL/REGIME
    (MODEL_REVERSAL_IGNORED, REGIME_CHANGE_IGNORED,
    LIQUIDITY_REVERSAL_IGNORED), RISK (RISK_DEVIATION), CONTEXT
    (STRATEGY_CONTEXT_LOSS, DUPLICATE_ECONOMIC_OUTCOME — batch-level).
  - `_support_state` (lines 148-159): OBSERVED / PROBABLE / CONFIRMED /
    INSUFFICIENT_EVIDENCE mapping from (confidence, evidence_count, required).
  - `BehaviorDetectionEngine.analyze` (lines 215-597): the evidence-gated
    detector set over explicit inputs:
    - PROFIT_GIVEBACK: giveback ≥ 0.60 AND mfe_r > 0 (confidence 0.5 + excess).
    - EARLY_EXIT_PATTERN: mfe_r ≥ 1.0 AND realized > 0 AND capture < 0.35.
    - LATE_EXIT_PATTERN: duration > 3× expected AND expected > 0 AND
      realized ≤ 0 (HIGH); OVERHOLD_LOSER additionally requires |mae_r| ≥ 0.5
      AND duration ≥ 900s.
    - EXCESSIVE_HOLD_TIME: robust strategy baseline (median+MAD); z =
      (duration − median)/MAD ≥ 3.0 with degenerate-MAD fallback
      max(median×0.25, 1.0) — LOW severity, confidence 0.5 + z/10 capped 0.9.
    - MISSED_BREAKEVEN: mfe_r ≥ 0.30 AND mae_r ≤ −0.30 AND realized < 0 AND
      NOT sl_moved (HIGH).
    - PREMATURE_BREAKEVEN: sl_moved AND mechanism in (BREAK_EVEN_SL_HIT,
      RISK_FREE_SL_HIT) AND mfe_r ≤ 0.20 AND duration ≥ 120s (MEDIUM) — BE
      inside normal noise.
    - MODEL_REVERSAL_IGNORED: model_flip ≥ 1.0 AND conf_at_exit ≤ 0.30 AND
      duration ≥ 60s AND realized < 0 (HIGH).
    - REGIME_CHANGE_IGNORED: regime_flip ≥ 1.0 AND regime_at_exit AND
      duration ≥ 300s AND realized < 0 (HIGH).
    - LIQUIDITY_REVERSAL_IGNORED: sweep_opposite AND duration ≥ 300s AND
      realized < 0 (HIGH).
    - RISK_DEVIATION: |actual−intended|/intended > 0.15 (MEDIUM; confidence
      0.5 + deviation capped 0.95).
    - EXIT_CLASSIFICATION_ANOMALY: mechanism RISK_FREE/BREAK_EVEN_SL_HIT with
      sl_moved=False (MEDIUM, 0.9) — SL geometry contradicts classification.
    - STRATEGY_CONTEXT_LOSS: record present but empty strategy_id (MEDIUM).
  - `analyze_record` (lines 599-625): convenience wrapper extracting fields
    from an ExperienceRecord.
  - Persistence: `persist` (lines 651-679) — content-addressed dedup key
    `beh_<sha256(ticket|pattern|canonical-evidence-json)[:16]>` with
    ON CONFLICT DO NOTHING, so identical observations never duplicate.
  - `analyze_canonical_trades` (lines 723-859): the offline/background driver
    (NEVER on the tick hot path). Reads `audit_ledger` rows with
    status != 'OPENED' (bounded newest 200), normalizes each via
    accounting.normalize, skips tickets already analyzed under the current
    versions (idempotency set from behavior_analysis), computes evidence
    coverage + the robust baseline per strategy, runs the engines + trade data
    anomalies, persists detections/anomalies/analysis, drains the audit queue
    (bounded join — safe offline) so callers observe records immediately.
    Returns analyzed/skipped/flags/anomalies/coverage summary.
  - `_trade_data_anomalies` (lines 902-1020): deterministic anomalies —
    STRATEGY_CONTEXT_LOSS, EXIT_CLASSIFICATION_ANOMALY,
    IMPOSSIBLE_EXCURSION (BUY with positive MAE / SELL with negative MFE),
    IMPOSSIBLE_TIMESTAMP (closed < opened). Anomaly id deterministic per
    (ticket, type, version) — TEST-ANOM-14/15 boundedness.
  - `_duplicate_outcome_anomalies` (lines 1023-1091): batch-level CRITICAL —
    GROUP BY execution_id HAVING count>1 among closed outcomes with PnL delta
    > 1e-9; idempotent against already-flagged anomaly ids under the version.
  - `BehaviorAnalysisBackfiller` (lines 1150-1183): bounded historical
    backfill driver (200 trades/run).
- HOT PATH / PERFORMANCE: offline only — worker cycle bounded (200 trades,
    30s interval, asyncio.to_thread); per-trade O(1) detectors plus one
    baseline query per strategy per batch; queue drained once per batch.
- EDGE CASES & PITFALLS:
  - ENG-DELTA vs Phase 08 flags: existing_flags is read then DISCARDED
    (`_ = set(existing_flags or [])` — line 249); the Phase 09 engine derives
    its own patterns from canonical ledger evidence and never consults the
    Phase 08 behavioral flag list — two parallel flag universes (intentional
    per docstring, but consumers must treat them as orthogonal).
  - `_strategy_hold_baseline` (lines 873-899) reads EXPERIENCE records via a
    NEW ExperienceLedger instance per strategy per batch and reads
    `holding_duration_seconds` ATTR that ExperienceRecord does not define —
    getattr returns 0.0 for every record → durations=[] → baseline None → the
    EXCESSIVE_HOLD_TIME detector never fires from the batch path (it fires
    only when callers pass baselines explicitly). ATTRIBUTE DRIFT BUG (see
    findings).
  - `analyze_canonical_trades` gracefully skips rows whose ticket matches
    done_tickets; a ticket with a FAILED prior analysis is skipped forever
    under the same versions (no retry path in this module).
  - `expected_duration_sec` in the batch path defaults to 600.0 when the
    baseline is empty (line 801) — the overhold detectors then compare against
    a fabricated 10-minute horizon; documented behavior, a source of plausible
    but synthetic expectations when real baselines are missing.
  - `_giveback_fraction` (lines 862-870) uses net_pnl vs mfe_usd — a negative
    net produces giveback ≈ 1.0 (min(1.0, (mfe−net)/mfe)) which then triggers
    PROFIT_GIVEBACK even though the trade never was in profit at exit;
    acceptable per measure but worth noting.
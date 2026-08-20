# src/nexus_scalp/experience/evaluator.py

- PURPOSE: Statistical scoring, confidence calibration, lifecycle transitions
  and the SELF-HEALING rebuild of the derived `strategy_intelligence_registry`
  from the immutable ledger. Everything here is REBUILDABLE — the registry is a
  cache, never a source of truth.
- ARCHITECTURE LAYER: Application (derived intelligence over ledger reads;
  persists via the audit queue; direct sqlite reads for the registry).
- RESPONSIBILITY: Enforce the Phase 08 statistical discipline (docstring lines
  10-26): SAMPLE-AWARE (retirement needs `min_samples_retire` closed trades
  AND a significant negative t-stat — one bad trade can never retire a
  strategy), RISK-AWARE (drawdown normalized by √n), RECENCY-AWARE (exponential
  decay + explicit recent window), BOUNDED (confidence capped at 0.95),
  REPLAY-GATED (VALIDATED/ACTIVE require an out-of-sample split: older half
  trains belief, newer half must confirm), PROBATION (retired families recover
  only after `min_samples_validated` new samples with a strong positive recent
  edge).
- DEPENDENCIES: numpy, `audit_repository.AuditRepository`, `experience.ledger`
  (ExperienceLedger), `experience.models` (StrategyScore, StrategyLifecycle,
  MAX_STRATEGY_CONFIDENCE, ExperienceRecord), stdlib json/math/sqlite3.
- CONNECTS TO: `intelligence.py` (gate scoring + self_heal -> rebuild),
  web/diagnostics (list_registered_scores), the worker-equivalent rebuild on
  startup. Ledger table consumed: audit_experiences + outcomes (via merged
  reads), strategy_intelligence_registry (cache).
- KEY CONCEPTS:
  - Tunables in `__init__` (lines 58-84): min_samples_evaluating=5,
    min_samples_validated=20, min_samples_retire=12, decay_half_life_trades=30,
    recent_window_trades=10, retire_expectancy_threshold_r=-0.20,
    retire_normalized_drawdown_r=3.0, retire_t_stat_threshold=-1.65,
    degrade_expectancy_threshold_r=0.0, recovery_expectancy_threshold_r=0.20,
    recovery_confidence_threshold=0.60.
  - `evaluate_strategy` (lines 90-257): ONLY executed AND closed experiences
    contribute outcome statistics (rejected proposals remain for forensics but
    are not trade evidence); closed sorted oldest→newest; win/loss by >0.05 /
    <−0.05 R (mirrors BREAKEVEN_R_BAND). Stats computed: expectancy (mean r,
    mean pnl), t-stat = mean/(std/√n) (0 when n≤1 or degenerate std),
    profit_factor with gross>0 guard (min(gross,99) when no losses else 1.0),
    max drawdown via cumulative sum, normalized by √n, P5 downside tail (min
    when n<5), recency weights exp(−age·ln2/half_life) normalized, recent-N
    window mean, replay split, mean quality scores (clamped ±1), flag counts,
    evidence_quality = 0.65·sample_factor + 0.35·stability where
    sample_factor=min(1, n/20) and stability=1/(1+√var).
  - `_confidence_score` (lines 303-340): raw = 0.28·sample_factor (n/(2·20))
    + 0.22·stability + 0.22·consistency ((recency_expectancy+1)/2) +
    0.16·drawdown_factor (1/(1+dd)) + 0.12·execution_factor; +0.05 when replay
    validated; recent_window_expectancy<0 subtracts up to 0.30; clamped
    [0, 0.95].
  - `_replay_split` (lines 279-295): requires ≥ min_samples_validated; split =
    n//2; validated iff BOTH halves' mean > 0.
  - `_determine_lifecycle_state` (lines 346-437): ordering matters — probation
    recovery evaluated BEFORE retirement (a retired family can graduate out on
    genuinely new evidence: ≥20 samples, recent r > 0.20, recency-weighted r >
    0, confidence ≥ 0.60 → EVALUATING, probation reset); retirement needs
    sample floor AND (persistent negative: recency≤−0.20 AND expectancy≤0 AND
    t≤−1.65) OR (catastrophic drawdown: normalized dd ≥3.0 AND expectancy<0);
    <5 samples DISCOVERED; recent OR recency-weighted < 0.0 → DEGRADED; <20
    EVALUATING; replay failure stays EVALUATING; recency>0 AND confidence≥0.50
    → ACTIVE else VALIDATED.
  - `rebuild_derived_intelligence` (lines 443-486): SELF-HEALING — enumerates
    strategy ids (≤5000), CLEARS the registry (cache only), replays each family
    (≤2000 experiences) with current_state=None so lifecycle is re-derived
    purely from immutable evidence; historical outcomes are only ever read.
  - `_persist_strategy_score` (lines 509-566): upsert into
    strategy_intelligence_registry through the audit queue — NOTE it writes
    `score.recent_window_expectancy_r` into the column named
    `recent_expectancy_r` (line 542) and omits recency_weighted from the row.
    `get_registered_strategy_score` (single PK read, parses score_payload JSON)
    and `list_registered_scores` (bounded, newest first) are the read paths.
- HOT PATH / PERFORMANCE: evaluation is O(n) numpy over ≤top_k experiences;
    only runs on cache miss (TTL 30s, ≤4/s inline budget in intelligence.py) or
    explicit rebuild. Registry reads are single indexed PK SELECTs. Rebuild is
    N families × bounded reads — safe but not tick-path material.
- EDGE CASES & PITFALLS:
  - CONSISTENCY BUG: `_persist_strategy_score` stores
    `recent_window_expectancy_r` in the `recent_expectancy_r` column while the
    full-precision value lives in score_payload JSON; web/diagnostic consumers
    reading the COLUMN display a rounded recent-window value, not the true
    recency-weighted figure — payload is authoritative.
  - `_mfe_r`/`_mae_r` fall back to |points|/planned_risk only when the
    normalized value is ≤ 0.0 — a genuinely zero MAE/MFE (never adverse /
    never favourable) is recomputed from points and can produce 0.0 again;
    harmless.
  - Empty-experience evaluation writes a DISCOVERED/previous-state score row
    (persist=True path) — a registry row can therefore exist with
    sample_count=0; the gate's registry fallback requires sample_count>0
    (intelligence.py line 476) so this cannot accidentally validate anything.
  - `_clear_registry` is a synchronous DELETE on a direct connection — called
    from rebuild; safe because all rows are recomputable, but it is the ONLY
    destructive SQL in the module.
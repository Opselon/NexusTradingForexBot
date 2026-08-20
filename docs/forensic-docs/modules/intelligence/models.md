# src/nexus_scalp/intelligence/models.py

- PURPOSE: PHASE 09 immutable domain contracts for position lifecycles, trade
  autopsies, measurable behavior detection and strategy evolution — the DERIVED
  intelligence layer on top of the authoritative Phase 08 experience ledger.
- ARCHITECTURE LAYER: Domain (inner ring) of the intelligence package. Pure
  Pydantic; the layer statement (docstring lines 7-12): experience IS the
  source of truth (immutable decisions/outcomes), intelligence IS the
  rebuildable interpretation (lifecycle, autopsy, behavior, evolution
  candidates).
- RESPONSIBILITY: Enforce the SAFETY CONTRACT (docstring lines 14-19) —
  nothing defined here can place, modify or close an order; the package only
  analyzes, scores, recommends and rejects BEFORE execution through the
  existing bounded gate; no adapter, no order manager, no risk engine.
- DEPENDENCIES: pydantic v2 (ConfigDict, Field, field_validator), stdlib
  (datetime, enum.StrEnum). Zero imports from experience/ or adapters — these
  contracts are self-contained.
- CONNECTS TO: `lifecycle.py` (PositionLifecycleEvent), `autopsy.py`
  (TradeAutopsy), `behavior.py` (BehaviorDetection / BehaviorAnalysis /
  AnomalyEvent), `evolution.py` (EvolutionCandidate), `gate.py`
  (SuitabilityTier lives here? No — gate.py defines its own; models.py holds
  the shared ones), `store.py` (typed reconstruction from payloads).
- KEY CONCEPTS:
  - `PositionEventType` (lines 31-43): the position-timeline observation
    vocabulary — CREATED → OPENED → MOVING (throttled) → EXPECTATION_CONFIRMED
    → MFE_REACHED → PROFIT_GIVEBACK → DEGRADING → RECOVERY_ATTEMPT → EXITED,
    plus MODIFIED.
  - `AutopsyVerdict` (lines 46-54): CLEAN_WIN / LUCKY_WIN / MANAGED_LOSS /
    COSTLY_LOSS / UNKNOWN / EVEN — the "why did this trade win/lose" model
    (narrative building lives in autopsy.py).
  - `EvolutionStatus` (lines 57-64): DISCOVERED → BACKTESTING → VALIDATED /
    REJECTED, and PROMOTED — a candidate is never live until validated AND
    operator-promoted.
  - `BehaviorSeverity` (lines 67-73): LOW/MEDIUM/HIGH/CRITICAL.
  - `MarketContext` (lines 76-87): bounded snapshot attached to every
    lifecycle event (symbol, session, regime, volatility, ATR, spread).
  - `PositionSnapshot` (lines 90-101): entry/current price, volume, SL/TP,
    floating + realized PnL at the event instant.
  - `PositionPerformance` (lines 104-114): MFE/MAE, peak profit/loss,
    profit_giveback_pct (ge=0), holding duration.
  - `DecisionContext` (lines 117-127): the decision identity that produced the
    position (strategy id/version, feature schema, model version, confidence,
    probability) — carried on every event so replay reconstructs WHY.
  - `PositionLifecycleEvent` (lines 130-160): one immutable, self-describing
    observation; `event_key` is the deterministic dedup key
    (ticket|sequence|type, built in lifecycle.py); `validate_utc` upgrades
    naive→UTC.
  - `TradeAutopsy` (lines 163-211): the forensic narrative object — packages
    the Phase 08 decomposition dimensions (strategy/entry/management/exit/
    execution quality, each bounded [-1,1]) + mfe_r/mae_r/giveback_pct +
    verdict + behavioral_flags + narrative, keyed by ticket.
  - `BehaviorDetection` (lines 214-236): ONE measurable pattern per record —
    NEVER an emotional attribution; carries behavior_id, pattern, severity,
    confidence, and an evidence dict (threshold/actual/expected/explanation
    contract used by behavior.py).
  - `EvolutionCandidate` (lines 239-265): a discovered variation (hypothesis +
    parameter_delta + pattern_evidence) that NEVER affects live trading until
    backtested and validated; source_strategy_id + candidate_id keyed.
  - `BehaviorAnalysisStatus` (lines 268-277): truthful lifecycle —
    NOT_ANALYZED / ANALYZING / ANALYSIS_FAILED / INSUFFICIENT_EVIDENCE /
    CLEAR / FLAGS_FOUND / ANOMALIES_FOUND (never "silent nothing").
  - `BehaviorAnalysis` (lines 280-310): one derived analysis record per
    canonical closed trade; idempotency identity = (ticket, behavior_version,
    anomaly_version) — same versions over identical data MUST produce the same
    record, never a duplicate; evidence_coverage + complete/partial_context
    make a zero-flag record at 20% coverage distinct from one at 100%.
  - `AnomalyEvent` (lines 313-335): one objective inconsistency with
    measurable evidence (anomaly_type, category, severity, confidence,
    evidence) — never "something unusual happened", always a specific
    contradiction.
- HOT PATH / PERFORMANCE: Pure model definitions — construction cost only,
    bounded by event/analysis volumes (events throttled in lifecycle.py).
- EDGE CASES & PITFALLS:
  - Defaults: many fields default to "" / 0.0 — the derived layer does not
    distinguish "0" from "unavailable" as rigorously as the experience layer;
    consumers should read evidence_coverage / reconstruction sources before
    treating zeros as facts.
  - `TradeAutopsy` has no model_validator enforcing ticket/strategy_id
    presence — the engine constructs it, not external callers.
  - Version fields (behavior_version/anomaly_version) are plain strings;
    version drift (bumping thresholds without bumping versions) silently
    re-keys analysis without changing semantics — the versioning discipline is
    convention-enforced by behavior.py constants, not by the model.
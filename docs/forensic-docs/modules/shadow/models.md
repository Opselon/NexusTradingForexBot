# src/nexus_scalp/shadow/models.py

- PURPOSE: PHASE 11 immutable shadow domain contracts (spec 2/5/21/22/23):
  one parallel decision, one bounded run, multi-dimension comparison,
  explainable promotion evaluation with vetoes, evidence-status ladder.
  CHALLENGER HAS ZERO EXECUTION AUTHORITY; every shadow artifact is
  explicitly SHADOW/SIMULATED and can never be confused with real
  account PnL.
- ARCHITECTURE LAYER: Domain (contracts).
- RESPONSIBILITY: ShadowDecisionKind, ShadowEvidenceStatus, ShadowModelRef,
  SharedInputRef (same-input proof), ShadowDecisionRecord, ShadowRun,
  ShadowComparison, PromotionEvaluation.
- DEPENDENCIES: pydantic, experience.models (CANONICAL_FEATURE_DIMENSION
  / CANONICAL_FEATURE_SCHEMA_ID), datetime.
- CONNECTS TO: shadow engine/store/comparison/worker, governance
  alignment (feature_hash), replay tooling, UI.
- KEY CONCEPTS:
  - ShadowModelRef: model identity + feature schema + artifact hash +
    is_champion flag; frozen.
  - SharedInputRef: PROOF OF SAME-INPUT — timestamp/symbol/timeframe/
    feature_hash/schema/dimension/regime/session/configuration_version;
    matches() compares all the identity fields; mismatched inputs mark a
    comparison INVALID_COMPARISON, excluded from promotion statistics.
  - ShadowDecisionRecord: full IDENTITY/MODEL/FEATURE/MARKET/DECISION/
    RISK/OUTCOME envelope; hypothetical_* fields are PURELY SIMULATED
    (simulated=True ALWAYS — a Field description asserts it is always
    true, but it is not hard-coded to True; pydantic will accept False,
    see pitfalls); hypothetical_risk_pct/volume/sl/tp + entry/exit/pnl/
    r/mfe_r/mae_r/holding_duration_sec/exit_reason; action_agreement +
    valid_comparison + invalid_reason.
  - ShadowRun: bounded run lifecycle RUNNING/COMPLETED/FAILED/CANCELLED
    with decision_count and error; started/finished timestamps.
  - ShadowComparison: multi-dimension aggregate — sample counts,
    action_agreement_rate, champion/challenger expectancy & drawdown (R),
    profit_factor, tail losses (r <= -1.5), mfe/mae, holding, calibration,
    avg confidence, plus by_regime/by_strategy/by_session breakdowns and
    best/worst/degraded/improved lists; evidence_status ladder
    (INSUFFICIENT_EVIDENCE/EVALUATING/PROMOTION_ELIGIBLE/REJECTED) with
    samples_required (default 30) / samples_observed; convenience
    properties expectancy_delta / drawdown_delta.
  - PromotionEvaluation: explainable promotion verdict — performance/
    risk/drawdown/oos/robustness/calibration/stability deltas +
    strategy_regression_penalty + sample_confidence → final_score
    [0,1]; eligible + vetoes list + reasons. A single critical VETO
    overrides the aggregate score.
- HOT PATH / PERFORMANCE: pydantic construction per decision on the live
  path — field validation is O(fields), negligible at M1 cadence.
- EDGE CASES & PITFALLS: ShadowDecisionRecord.simulated is
  default=True but NOT enforced — a caller could construct a record with
  simulated=False and nothing here stops it (the enforcement lives in
  the engine/docstring only); ShadowComparison.calibration is a
  [0,1] score (1 - |acc-conf| per comparison.py) — the field name
  "calibration" could be confused with ECE-style calibration used
  elsewhere (governance.evidence); frozen models are serialized with
  model_dump(mode="json") by the store — list/dict fields become JSON.
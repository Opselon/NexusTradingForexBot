# src/nexus_scalp/intelligence/gate.py

- PURPOSE: The Phase 09 pre-trade intelligence gate — adds an explicit WARN
  tier and a bounded "suitability" score on top of the Phase 08 gate's
  ALLOW / PENALIZE / REJECT / INSUFFICIENT_EVIDENCE, producing a richer
  decision explanation for live explainability.
- ARCHITECTURE LAYER: Application (wraps ExperienceIntelligenceEngine;
  consumed by the live proposal pipeline before risk sizing / dispatch).
- RESPONSIBILITY (docstring lines 12-19, non-negotiable safety): the gate can
  only down-rank the confidence of, WARN on, PENALIZE or REJECT an EXISTING
  proposal — it never creates a proposal, never places/modifies a position or
  SL/TP, never bypasses RiskEngine or OrderManager; rejection is expressed as
  ActionType.NO_TRADE BEFORE order placement; absence of evidence is never
  approval (INSUFFICIENT_EVIDENCE passes the proposal bit-identical).
- DEPENDENCIES: `domain.enums.ActionType`, `domain.models.TradeProposal`,
  `experience.intelligence.ExperienceIntelligenceEngine`, `experience.models`
  (ExperienceAction, PreTradeExperienceDecision, StrategyLifecycle),
  features (MarketRegimeState, FeatureVector), observability.logging.
- CONNECTS TO: LiveEngine pipeline (runs AFTER the Phase 08 gate — a Phase 09
  rejection is strictly before risk sizing/dispatch, and it can only downgrade,
  never upgrade); web diagnostics via summary().
- KEY CONCEPTS:
  - `SuitabilityTier` (lines 44-56): standalone StrEnum (ExperienceAction
    cannot be extended at runtime) — ALLOW / WARN / PENALIZE / REJECT /
    INSUFFICIENT_EVIDENCE. WARN is the new Phase 09 tier.
  - `SuitabilityVerdict` (lines 59-84): decision + suitability_score [0,1] +
    qualifies + adjusted_confidence + reason + evidence dict; to_dict()
    round-trips for the API.
  - `evaluate` (lines 115-168): runs the Phase 08 gate first; REJECT /
    PENALIZE verdicts are lifted verbatim into suitability (`_verdict_from_phase08`),
    INSUFFICIENT_EVIDENCE passes through unchanged with suitability 0.0 and
    qualifies=True; otherwise `_evaluate_with_evidence` computes the Phase 09
    verdict.
  - `_evaluate_with_evidence` (lines 170-244): WARN when suitability ≤
    warn_suitability_floor (0.25) OR normalized drawdown ≥ severe_drawdown_r
    (2.5); WARN with NEUTRAL_OR_NEGATIVE_EXPECTANCY when expectancy_r ≤ 0.0;
    REJECT (soft) when suitability < min_suitability_to_qualify (0.40) —
    rewrites the proposal to NO_TRADE with decision_stage TRADE_INTELLIGENCE_GATE
    and blocked_by SUITABILITY_GATE. WARN never changes the proposal —
    it is informational for the operator.
  - `_suitability_score` (lines 250-277): base 0.5; + expectancy_r×0.6
    clamped ±0.30; + recent_expectancy_r×0.4 clamped ±0.15; − dd×0.10 capped
    0.30; + evidence_quality×0.2 capped 0.20; lifecycle adjustments
    (DEGRADED −0.20; ACTIVE/VALIDATED +0.10); sample-size penalties
    (0 samples −0.15, <5 −0.10); clamped [0,1].
  - `_evidence_dict` (lines 279-290): serialized evidence for explainability.
- HOT PATH / PERFORMANCE: adds pure arithmetic on top of the Phase 08 gate's
  cached evaluation — no additional DB work; per-proposal cost negligible.
- EDGE CASES & PITFALLS:
  - Ordering subtlety: a proposal with score < 0.40 is REJECTed even when
    drawdown/expectancy warned first (REJECT branch evaluated after WARN
    branch and wins) — intended: the qualify floor is the hard gate.
  - The suicitability score can REJECT where Phase 08 ALLOWED (score < 0.40
    with positive expectancy) — that is the point of the Phase 09 layer, but
    note the two gates are not coordinated on thresholds: a family with
    expectancy 0.05 + recent 0 + drawdown 2 + low evidence quality can score
    ~0.33 → rejected despite a positive Phase 08 verdict.
  - INSUFFICIENT_EVIDENCE verdict carries suitability_score 0.0 and empty
    evidence while qualifies=True — the API consumer must not interpret 0.0
    suitability as rejection (qualifies disambiguates).
  - `_suitability_score` reads `d.recent_expectancy_r` — which the Phase 08
    decision fills from `score.recent_window_expectancy_r` (intelligence.py
    line 329); the column-name drift documented in evaluator.md (recent vs
    recency-weighted) flows through here as the "recent" signal.
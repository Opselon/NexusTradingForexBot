# src/nexus_scalp/experience/intelligence.py

- PURPOSE: The Phase 08 pre-trade decision boundary plus post-trade outcome
  recorder — turns accumulated experience into a bounded, explainable verdict
  on a live trade proposal, and records closed-trade outcomes with full quality
  decomposition back into the memory layer.
- ARCHITECTURE LAYER: Application (orchestrates ledger/evaluator/retriever/
  analyzer; consumed by LiveEngine and the Phase 09 gate).
- RESPONSIBILITY: Enforce the five HARD SAFETY INVARIANTS (docstring lines
  7-31): (1) the gate may only DOWN-RANK or REJECT an existing proposal — no
  order capability, rejection expressed as ActionType.NO_TRADE strictly BEFORE
  order placement; (2) ONLY ENTRY actions are gated — CLOSE_POSITION /
  PARTIAL_CLOSE / MODIFY_SL_TP / CANCEL_ORDER pass untouched (the BUG-010 fix:
  the first revision gated every action so a retired strategy could suppress a
  protective close); (3) absence of evidence is never approval —
  INSUFFICIENT_EVIDENCE passes the proposal bit-identical; (4) learning failure
  is non-critical — any internal exception is isolated, logged, and the ORIGINAL
  proposal passes through unchanged with a failure reason; (5) hot-path
  discipline — score refreshes are TTL-cached and rate-limited (cache hit =
  zero DB work; exhausted budget → INSUFFICIENT_EVIDENCE, never a blocked loop).
- DEPENDENCIES: domain.enums.ActionType, domain.models.TradeProposal,
  experience evaluator/ledger/models/outcome_recovery/quality/retriever,
  features (MarketRegimeState, FeatureVector), observability.logging.
- CONNECTS TO: LiveEngine (evaluate_proposal in the pipeline before risk;
  record_trade_outcome at close); intelligence/gate.py wraps this engine
  (Phase 09 WARN tier); web status via summary(); ModelRegistry wiring via
  set_provenance.
- KEY CONCEPTS:
  - GATED_ENTRY_ACTIONS (lines 69-80): all 8 BUY/SELL market/limit/stop action
    types — deliberately excludes every position-management action.
  - `evaluate_proposal` (lines 166-213): NEVER raises — any exception becomes
    `action=INSUFFICIENT_EVIDENCE, qualifies_trade=True`, original proposal
    returned unchanged, gate_failure_count incremented.
  - `_evaluate_internal` (lines 215-388), the verdict ladder:
    1. Scope: disabled or non-entry → passthrough decision (strategy_id
       "strat_not_evaluated", reason GATE_DISABLED / NON_ENTRY_ACTION_NOT_GATED).
    2. Bounded context via build_proposal_context (same retriever derivation
       the rest of the system uses — no duplicated setup/session mapping).
    3. TTL-cached causally-valid evidence via `_get_score`.
    4. The immutable decision snapshot is ALWAYS recorded (before the verdict)
       — `_record_decision_experience` writes a FeatureSnapshot under the
       ACTIVE schema identity + current provenance and
       `idempotency_key = f"exp_{request_id}"` (build_idempotency_key,
       lines 593-602) so pre-trade write and post-trade outcome always agree.
    5. Verdict: INELIGIBLE_LIFECYCLES → REJECT (confidence 0.0, hard); DEGRADED
       → PENALIZE (confidence × 0.70) and re-REJECT if below 0.40 qualify
       floor; ACTIVE/VALIDATED with recency_weighted_expectancy_r >
       0.50 AND replay_validated → ALLOW_WITH_CONTEXT boost (×1.10, capped
       1.0) — boost requires BOTH recency edge and OOS confirmation, never
       sample count alone; DISCOVERED/EVALUATING → ALLOW with
       "EVIDENCE_ACCUMULATING".
    6. Rejection rewrites the proposal: action=NO_TRADE, confidence=0.0,
       rejection_reason, final_action, decision_stage=EXPERIENCE_INTELLIGENCE_GATE,
       blocked_by=EXPERIENCE_<LIFECYCLE>. Penalization sets confidence_before/
       after_filters + override_reason.
  - `_get_score` (lines 440-485): three tiers, cheapest first — (1) TTL cache
    hit (30s); (2) inline refresh (bounded retrieval + evaluation) while the
    ≤4/s budget allows; (3) when the budget is exhausted, a single indexed
    registry PK read so a RETIRED strategy can NEVER slip through (registry
    score requires sample_count>0), then stale cache, then None.
  - `refresh_strategy_score` (lines 487-515): retrieves top_k(100) experiences,
    filters closed ones, evaluates, caches. Safe to call from a background task.
  - `_record_decision_experience` (lines 528-573): feature values extracted
    dimension-agnostically (`_extract_feature_values` squeezes tensor /
    list-flattens) — a future 60D/350D schema needs no change here.
  - `record_trade_outcome` (lines 608-843): Phase 14 behavior —
    (a) request_id missing → `resolve_outcome_correlation` (POSITION_STATE /
    BROKER_TICKET_FALLBACK; logs CORRELATION_FAILED on failure); request_id
    present → ORIGINAL_REQUEST provenance stamped; (b) refuses to record when
    NO_DECISION_SNAPSHOT or when outcome_timestamp < decision_timestamp
    (causality); (c) `has_outcome` duplicate guard; (d) ANOMALY-VERIFY-01
    economic-identity guard via ledger.owner_of_execution — a second closed
    outcome carrying the same broker ticket under a DIFFERENT key is a
    split-fill/sibling-ticket duplicate (BUG-081 pattern) and is REJECTED;
    (e) decomposes via OutcomeAnalyzer (reentry measurement included), builds
    BrokerOutcome from the dict payload when valid, records, invalidates the
    score cache for the family. Fully exception-isolated.
  - `_build_execution_context` (lines 845-881): derives DIRECTIONAL slippage
    from (actual − expected) when the caller passed 0 — sign flips for SELL so
    "adverse" always means the same thing.
  - `self_heal` (lines 887-897): rebuild_derived_intelligence + cache clear.
  - `summary` (lines 899-918): observability snapshot (counters, schema
    distribution, provenance).
- HOT PATH / PERFORMANCE: gate path = context build + cache lookup + ONE queued
    decision write per proposal; refresh path bounded to ≤4/s globally;
    retrieval bounded to top_k=100; all DB writes queued. `summary()` runs
    COUNT queries — diagnostics only.
- EDGE CASES & PITFALLS:
  - When inline refresh budget is exhausted AND registry has no row, the gate
    returns stale cache or None → INSUFFICIENT_EVIDENCE (never blocks).
  - INCONSISTENCY: `build_proposal_context` hardcodes timeframe="M1" — context
    families are M1-only by construction at this call site (proposals for other
    timeframes would still be bucketed into an M1 family).
  - `_record_decision_experience` stores `model_probability=signal_confidence=
    proposal.confidence` — one value duplicated into two fields; consumers must
    treat them as the same quantity.
  - Correlation recovery returns BROKER_TICKET_FALLBACK deterministically
    (`exp_bt_<ticket>`) even when no decision row exists — the caller MUST then
    verify a matching decision before recording; record_trade_outcome does this
    via get_experience_by_key (NO_DECISION_SNAPSHOT refusal), so the fallback
    never fabricates evidence.
  - The `qualifies_trade=True` on the exception path with action
    INSUFFICIENT_EVIDENCE is deliberate: pass-through must not be confused
    with an endorsement (no confidence change).
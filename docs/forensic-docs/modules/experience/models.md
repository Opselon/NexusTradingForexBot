# src/nexus_scalp/experience/models.py

- PURPOSE: The frozen (immutable) domain contracts of the experience memory
  layer — the decision rows, append-only outcome events, corrections, scores,
  provenance and decomposition objects that PHASE 08 persists and consumes.
- ARCHITECTURE LAYER: Domain (inner ring). Pure Pydantic; zero infrastructure
  imports; the single source of schema truth for the experience subsystem.
- RESPONSIBILITY: Enforce the four design invariants declared in the module
  docstring (lines 6-27): (1) MODEL-AGNOSTIC MEMORY — records never embed model
  weights and never require the producing artifact to exist; (2) SCHEMA-VERSIONED
  FEATURES — every snapshot carries `feature_schema_id` + `feature_dimension`
  and validates length against the DECLARED dimension, so a 50D experience is
  never silently reinterpreted under a wider schema; (3) IMMUTABILITY —
  `frozen=True` everywhere, outcomes are separate rows, corrections are additive
  events; (4) MEASURABLE BEHAVIOR ONLY — no psychological labels anywhere.
- DEPENDENCIES: pydantic v2 (`ConfigDict`, `Field`, `field_validator`,
  `model_validator`), stdlib only otherwise (datetime, enum.StrEnum). No
  adapters, no database imports.
- CONNECTS TO: `ledger.py` persists `ExperienceRecord`/`ExperienceOutcome`
  payloads; `quality.py` produces `OutcomeDecomposition`; `evaluator.py` builds
  `StrategyScore`; `intelligence.py` mints `PreTradeExperienceDecision` and
  snapshots; `intelligence/*` models are a separate (derived) layer ON TOP and
  never replace these.
- KEY CONCEPTS:
  - Constants: CANONICAL_FEATURE_SCHEMA_ID="scalp_v1", dimension 50 (lines
    40-41) — the current live contract; future schemas (60D/350D) are additive
    and MUST NOT rewrite history. MAX_STRATEGY_CONFIDENCE=0.95 (line 45):
    confidence may never reach 1.0 regardless of sample size.
  - `StrategyLifecycle` (lines 48-70): DISCOVERED → EVALUATING → VALIDATED →
    ACTIVE, plus DEGRADED / RETIRED / QUARANTINED. Retirement/quarantine block
    NEW live decisions only; raw history for the strategy is always preserved.
    `INELIGIBLE_LIFECYCLES` (lines 74-76) = {RETIRED, QUARANTINED} drives the
    pre-trade gate's hard reject.
  - `ExperienceAction` (lines 79-88): ALLOW / ALLOW_WITH_CONTEXT /
    INSUFFICIENT_EVIDENCE / PENALIZE / REJECT. INSUFFICIENT_EVIDENCE is
    explicitly "passes through completely unchanged — never a fabricated
    endorsement".
  - `BehavioralFlag` (lines 91-120): 12 objectively computable decision-quality
    failure labels (ENTRY_CHASE … EXECUTION_SLIPPAGE_ANOMALY); deterministic
    definitions live in quality.py. `QualityVerdict` (GOOD/ACCEPTABLE/POOR/
    UNKNOWN) coarse verdicts.
  - `OutcomeClass` + BREAKEVEN_R_BAND=0.05 (lines 132-153): Phase 14 fix —
    BREAK_EVEN is a REAL outcome class, wins/losses are |r|>0.05, mirroring the
    evaluator thresholds so counts and classes always agree.
  - `ExitReason` (lines 156-180): canonical broker-close taxonomy; MANUAL_CLOSE
    is reserved for genuine broker DEAL_REASON_CLIENT closures with no
    protective context; values backwards-compatible with legacy
    `audit_ledger.exit_mechanism` strings.
  - `OutcomeCorrelationSource` (lines 183-192): ORIGINAL_REQUEST /
    POSITION_STATE / BROKER_TICKET_FALLBACK — never pretends a fallback is the
    original request id.
  - `FeatureSnapshot` (lines 195-230): `validate_dimension` rejects values
    whose length != declared dimension; `is_canonical_live_schema` flags
    50D/scalp_v1 rows.
  - `ModelProvenance` (lines 233-253): descriptive identity only — no tensors,
    no file handles; fingerprint-able artifact identity. Default model_id
    "unregistered" until ModelRegistry registers one.
  - `ExecutionContext` / `PositionBehavior` (lines 255-292): broker execution
    quality and fill-to-exit behavior; slippage_points signed against direction;
    mae_r/mfe_r normalized by planned risk.
  - `OutcomeDecomposition` (lines 295-322): 8 bounded [-1,1] quality scores
    + 3 verdicts; `profitable_for_wrong_reason` / `acceptable_loss` prevent
    "won = good" as the learning rule.
  - `StrategyContext` (lines 325-346): bounded hierarchical context fingerprint
    (strategy_id = deterministic family hash) — coarse on purpose so experiences
    aggregate into families instead of one strategy per float vector.
  - `ExperienceOutcome` (lines 349-387): append-only event keyed by
    idempotency_key; `correlation_source/detail` (Phase 14) document WHICH
    fallback recovered the experience; `broker_outcome` carries authoritative
    broker closure evidence. NOTE `execution_id` has `default=""`: the outcome
    table carries the broker ticket — by design the trade→strategy bridge.
  - `BrokerOutcome` (lines 390-423): broker-reported closure evidence,
    aggregated correctly across multiple close deals.
  - `ExperienceCorrection` (lines 426-437): additive correction event; history
    is never destroyed.
  - `ExperienceRecord` (lines 440-586): the immutable decision row. Hard
    invariants: frozen; `validate_causality` rejects outcome_timestamp <
    decision_timestamp; `_migrate_legacy_payload` lifts revision-1 flat
    `feature_vector_50d` payloads into canonical snapshots on read;
    `with_outcome()` returns a NEW projected copy — the stored row is never
    mutated. `planned_risk_distance` = |proposed_entry − stop_loss|.
  - `StrategyScore` (lines 589-649): DERIVED, rebuildable statistical evidence —
    "nothing in this object is a source of truth"; includes expectancys,
    profit factor, normalized (by √n) drawdown, recency-weighted expectancy,
    in/out-of-sample replay split, confidence_score ≤ 0.95, evidence_quality,
    probation_samples. `is_eligible_for_new_trades` = not in
    INELIGIBLE_LIFECYCLES.
  - `PreTradeExperienceDecision` (lines 652-676): explainable pre-trade verdict
    (action, qualifies_trade, adjusted_confidence, retrieved_sample_count,
    similarity, expectancy, drawdown, penalty_reason, provenance).
- HOT PATH / PERFORMANCE: Models are cheap dataclasses under pydantic v2;
  frozen validation cost is paid at construction. `with_outcome` copies the
  record (O(record size)) — used only on bounded retrieval paths.
- EDGE CASES & PITFALLS:
  - Zero-value semantics: 0.0 defaults mean "unavailable" IS expressed as 0.0
    in several scalar fields; the quality/repair layers later treat
    exactly-zero realized values as repair candidates — see outcome_repair.py.
  - `validate_utc` upgrades naive datetimes to UTC by REPLACING tzinfo (not
    converting) — a naive datetime is assumed already-UTC, never local time.
  - `BrokerOutcome.net_pnl_usd` is computed as gross − |commission| − |swap|
    (outcome_recovery.py) — a derived field, consumers should not recompute it.
# src/nexus_scalp/shadow/shadow70/models.py

- PURPOSE: Canonical, frozen contracts for the 70D shadow observation
  layer (TASK-05-70D-SHADOW): candidate contract, observation,
  provenance, vector report, disagreement taxonomy. Safety: frozen
  models only; no execution/risk/policy objects (INV-018).
- ARCHITECTURE LAYER: Domain (contracts).
- RESPONSIBILITY: constants (SHADOW70_SCHEMA_ID, SHADOW70_DIMENSION,
  BASE/NEWS/LIQUIDITY dims + slices, LIQUIDITY_FEATURE_NAMES,
  OUTPUT_CLASSES), Shadow70RuntimeState, Shadow70LoadStatus,
  DisagreementClass, Shadow70CandidateContract, Shadow70FeatureProvenance,
  Shadow70VectorReport, Shadow70Observation, classify_disagreement.
- DEPENDENCIES: pydantic, math, datetime.
- CONNECTS TO: shadow70 runtime/store/health/worker/liq_provider,
  governance alignment conventions, forensics (LIQUIDITY_FEATURE_NAMES
  mirrors forensics/references LIQUIDITY_70D_FEATURE_NAMES).
- KEY CONCEPTS:
  - SCHEMA CONTRACT: SHADOW70_SCHEMA_ID = "scalp_v3" (canonical 70D id
    restored after AGENT-10 temporarily moved to scalp_v4); dimension 70
    = BASE_SLICE (0,50) + NEWS_SLICE (50,60) + LIQUIDITY_SLICE (60,70);
    LIQUIDITY_FEATURE_NAMES = the 10 canonical names at indices 60..69.
  - Shadow70CandidateContract.is_validated() == VALIDATED_CANDIDATE;
    is_70d() == dimension 70 AND schema scalp_v3.
  - Shadow70Observation: idempotent (deterministic observation_id =
    sha256_json("{snapshot_id}|{model_id}|{model_version}|{ts}") via the
    runtime), simulated=True, disagreement + agreement flags, bounded
    evidence (regime/session/news/liquidity state + liquidity_features_10;
    full 70D only under debug), outcome field PENDING (research only —
    NEVER feeds accounting or the experience ledger, INV-018); probability
    vectors validated finite.
  - classify_disagreement — precedence order (pure, never raises):
    same action → CONFIDENCE_DIVERGENCE if |conf diff| >= 0.10 else
    AGREEMENT; both trade, opposite direction → BUY_VS_SELL; both trade,
    same dir → ACTION_DISAGREEMENT; champion trades / shadow NO_TRADE →
    CHAMPION_BUYS/SELLS_SHADOW_NO_TRADE; shadow trades / champion
    NO_TRADE → CHAMPION_NO_TRADE_SHADOW_BUYS/SELLS; direction
    disagreement fallback → DIRECTION_DISAGREEMENT; else
    NO_TRADE_DISAGREEMENT. DisagreementClass includes the detailed
    spec-26 categories + HIGH/LOW_CONFIDENCE_DISAGREEMENT members.
- HOT PATH / PERFORMANCE: pydantic per observation; finite validation
  O(len(probs)).
- EDGE CASES & PITFALLS: agreement semantics are defined by the RUNTIME
  (agreement = AGREEMENT or CONFIDENCE_DIVERGENCE), not by the model —
  two "action-disagreeing" observations are never agreements even at
  equal confidence; classify_disagreement's last branches are largely
  unreachable given the earlier guards (defensive); the confidence
  divergence branch only fires on equal actions — a divergence on
  disagreeing actions stays ACTION/BUY_VS_SELL; outcome resolution is
  not implemented in this package (field exists; resolved elsewhere).
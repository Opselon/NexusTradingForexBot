# src/nexus_scalp/experience/retriever.py

- PURPOSE: Bounded context fingerprinting + causal top-K retrieval — builds a
  `StrategyContext` from live market state and pulls historically relevant
  experiences for a decision timestamp.
- ARCHITECTURE LAYER: Application (read-side intelligence over the ledger).
- RESPONSIBILITY: Two hard invariants (docstring lines 16-23): (1) every
  retrieval is bounded by `top_k` AND the ledger's MAX_RETRIEVAL_LIMIT — no
  unbounded table scan on any tick; (2) every retrieval filters
  `decision_timestamp < decision_timestamp_of_now` — future experiences can
  never inform a past decision. Plus the BUG-009 fix: `build_context` NEVER
  mutates the caller's `confluence_tokens` list (repeated calls with a shared
  list produced a drifting fingerprint and different strategy_id for identical
  market state).
- DEPENDENCIES: `experience.ledger.ExperienceLedger`, `experience.models`,
  `features.regime_classifier.MarketRegimeState`, `features.scalp_features.
  FeatureVector`, observability.logging.
- CONNECTS TO: `intelligence.py` (build_proposal_context + retrieve relevance
  on the gate path; refresh_strategy_score), evaluator (via ledger reads),
  and tests. SETUP_FAMILIES constant (lines 42-47) is the canonical entry-reason
  taxonomy: SMC_GOD_MODE / FAST_LIQUIDITY_SWEEP / PREDICTIVE_LIMIT / PURE_AI.
- KEY CONCEPTS:
  - Context classifiers (all pure, deterministic bucketers that make
    aggregation into families possible instead of one strategy per float
    vector):
    - `classify_volatility`: ATR buckets — ≤0 UNKNOWN, <0.80 LOW, >3.0
      EXTREME, >2.0 HIGH, else NORMAL.
    - `classify_session`: overlap checked FIRST (LONDON/NY overlap is the most
      specific state) → LONDON → NY → TOKYO → OFF_SESSION; None → "ALL".
    - `classify_trend`: htf_h4_trend sign → BULLISH/BEARISH/NEUTRAL.
    - `classify_setup`: substring scan of "{reason} {mode}".upper():
      SMC_GOD_MODE → SWEEP → LIMIT → non-blank blob → PURE_AI → UNCLASSIFIED.
    - `build_confluence_fingerprint`: works on a LOCAL set copy — caller's
      list never mutated; folds FVG/CHOCH/order_block/liquidity_sweep tokens
      into a sorted-join digest.
  - `build_context` (lines 149-189): assembles the bounded context fields and
    computes the deterministic family id via
    `ledger.generate_strategy_id()` — same market state always maps to the
    same strategy_id, making aggregation and pre-trade gating reproducible.
    strategy_version parameter feeds into the hash (a versioned context family
    is a distinct family).
  - `retrieve_relevant_experiences` (lines 195-240): exact family match first
    (similarity=1.0, the common indexed path); if empty, hierarchical fallback —
    symbol-scoped bounded scan (2×top_k) keeping only contexts with similarity
    ≥ MIN_GENERALIZED_SIMILARITY (0.60), sorted desc, top-k kept, average
    similarity returned. Empty list + 0.0 similarity means "no evidence" —
    the gate MUST treat it as INSUFFICIENT_EVIDENCE, never approval.
  - `_calculate_context_similarity` (lines 242-271): weighted equality,
    weights: symbol 0.15, regime 0.25, trend 0.20, volatility 0.15, session
    0.10 (OR "ALL" wildcard — "ALL" matches either side), setup 0.15. Regime
    and trend dominate because they decide whether historical outcome is
    relevant at all.
- HOT PATH / PERFORMANCE: exact-match path is one indexed query (strategy_id)
    + bounded limit; hierarchical fallback is 2×top_k symbol rows + O(n)
    similarity scoring per gate refresh — that is why it is limited to the
    TTL-cached refresh tier (30s TTL, ≤4/s rate) in intelligence.py, never the
    per-tick path.
- EDGE CASES & PITFALLS:
  - Confluence digest only contains TOKENS PRESENT — absence means "no
    confluence evidence", encoded by the EMPTY string, which still participates
    in the strategy_id hash: a bare state is a distinct family from any
    confluence state.
  - Similarity treats the 6 dimensions as equal-weight equality checks;
    `confluence_fingerprint` and `parameter_hash` do NOT participate in the
    similarity score — two families differing only in confluence hash behave
    as exact-stage matches only when their strategy_ids collide (they will not)
    or fall back to symbol-scope similarity where confluence is invisible. A
    documented limitation, not a bug.
  - SETUP_FAMILIES is declared but unused in this module (classify_setup uses
    its own substring rules) — a latent drift risk if the taxonomy changes.
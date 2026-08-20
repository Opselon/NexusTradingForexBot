# src/nexus_scalp/governance/alignment.py

- PURPOSE: Shadow input alignment & same-state parity (TASK-6 spec 5-8):
  guarantee BOTH models saw EXACTLY the same market state at the same
  timestamp, and record parity evidence per comparison. The Champion
  input is NEVER mutated (challenger vector is a fresh list copy).
- ARCHITECTURE LAYER: Domain (governance input-integrity boundary).
- RESPONSIBILITY: challenger_input_for (build the challenger vector by
  EXTENDING the champion 50D — never truncate/reorder/pad), sha256_json /
  news_context_hash (canonical identity hashes), vectorize_news_context
  (12-field canonical news mapping), feature_parity (MAX_ABS_DIFF /
  MEAN_ABS_DIFF / MISMATCH_COUNT), build_shadow_parity.
- DEPENDENCIES: governance.models (ShadowParity), hashlib, logging.
- CONNECTS TO: GovernanceShadowRuntime.compare, shadow70 news_provider
  (NEWS_CONTEXT_DIM), model_generation vectorization conventions,
  replay/parity tests.
- KEY CONCEPTS:
  - Schema contract: ALLOWED_SCHEMA_IDS=(scalp_v1, scalp_v2, scalp_v3).
    For scalp_v2 challengers: 60D = 50D + 10 RESERVED scalp_v2 extras
    (leading 10 slots so v1 bytes stay identical), or 72D = + 12 news
    fields when build_metadata.input_dimension says so.
  - `extras_60d` MUST be supplied for a scalp_v2 challenger — zero-fill
    is REFUSED (raising) because it feeds a distribution the model never
    trained on (TASK-5 compute_60d_extras contract).
  - Alignment verdicts: "IDENTICAL" (same schema/dim, byte-equal copy) or
    "NEWS_EXTENDED"; any other schema pair raises (no silent compatibility).
  - sha256_json convention: hashlib.sha256(str(payload).encode())[:16] —
    deterministic content hash used for news_context_hash (whole snapshot
    keys: available/state/active_event_count/relevance/bullish/bearish/
    confidence/conflict/freshness/consensus/stale/active_high_impact/
    timestamp) and observation identity (shadow70).
  - feature_parity: reference None → parity UNKNOWN (parity_ok False,
    never assumed OK); empty vectors → EMPTY; else per-index abs diff
    with tolerance 1e-6; parity_ok requires zero mismatches AND equal
    lengths (a shorter comparison is MISMATCH, not OK).
  - build_shadow_parity treats UNKNOWN state as ok=True (recorded
    flagged) so absence of a replay reference does not invalidate live
    comparisons — but MISMATCH does (consumed by shadow_runtime).
- HOT PATH / PERFORMANCE: per-comparison O(n) diff on a live tick path —
  trivially cheap at 50-72 floats.
- EDGE CASES & PITFALLS: champion vector empty → ValueError; vectorize
  handles dict or object contexts (getattr fallback); state/novelty
  encodings mirror model_generation exactly (must stay in lockstep);
  news_context_hash of None is "no_news_context" hash, not zero.
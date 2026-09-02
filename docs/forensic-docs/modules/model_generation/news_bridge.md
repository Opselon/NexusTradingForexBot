# src/nexus_scalp/model_generation/news_bridge.py

- **PURPOSE:** The news↔model bridge — converts raw news rows into the
  canonical news context (news_context_v1 12-field vector) and the 10-field
  70D news block. The bridge is WHERE news honesty is enforced: events that
  postdate a dataset's decision time are encoded as the ZERO vector with
  NEWS_INCONCLUSIVE_NO_OVERLAP state — never fabricated.
- **ARCHITECTURE LAYER:** ML research (dataset/feature bridge).
- **RESPONSIBILITY:** (a) normalize raw news frames (`normalize_news_frame`
  — column coercion, publication-ts normalization); (b) `news_context_at`
  — the causal snapshot: given a decision timestamp, compute the 12-field
  vector (active_high_impact_events, xauusd_relevance, usd_relevance,
  bullish_pressure, bearish_pressure, conflict_score, novelty, freshness,
  confidence, source_consensus, news_state, time_since_event_sec) using
  ONLY events published ≤ decision time; (c) `build_news_frame_from_db` —
  load + normalize from news.db; (d) `_derive_news_state` — the state
  encoding (NO_OVERLAP/ACTIVE/DECAYED); (e) field encoding helpers
  (`_encode_state`, `_encode_novelty`, `_num` with explicit defaults).
- **DEPENDENCIES:** polars, news models (NewsContextSchema), datetime
  epoch helpers (`_safe_epoch_sec`, `_parse_iso`).
- **CONNECTS TO:** schema_v2 dataset builders (60D/70D paths),
  features/schema_contract (NEWS_10D_NAMES equality assertion),
  features/features70 (news_10d_from_context), tests
  (test_news_bridge_phase13b, test_news_bridge_contract_phase13b,
  test_news_bridge_finalize_phase13b, test_news_keywords_dataset).
- **KEY CONCEPTS:** Causality is the contract: every field at decision time
  t reflects only events with publication ≤ t; the 70D block selection
  (fields 0..8 + news_state idx 10) matches schema_contract EXACTLY
  (asserted at import); `_safe_epoch_sec` normalizes mixed datetime formats
  (epoch vs ISO vs naive) — the epoch-bug discipline.
- **EDGE CASES & PITFALLS:** No events → zero vector + NO_OVERLAP (model
  sees neutral news, never fabricated); malformed rows are coerced to
  defaults (never dropped silently — counts logged); timezone-naive
  publication timestamps are treated as UTC (documented).
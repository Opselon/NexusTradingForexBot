# src/nexus_scalp/model_generation/sample_factory.py

- **PURPOSE:** Sample Factory (PHASE 13, spec 15/16): builds deterministic,
  provenance-preserving samples from raw market history + news. Deterministic
  sample identity with no future information.
- **ARCHITECTURE LAYER:** Research/ML — dataset construction, no order authority.
- **RESPONSIBILITY:** RAW MARKET HISTORY → bar normalization → features
  (FeatureSchemaRegistry) → regime → causally-correct news snapshot → setup
  builder (deterministic rules) → TripleBarrierLabeler (3-class) → Sample.
- **DEPENDENCIES:** polars, features.schema.FEATURE_SCHEMAS, labeling.triple_
  barrier.TripleBarrierLabeler, models (LabelSchema, NewsContextSchema,
  SampleContract, SetupContract), news_bridge (lazy), sample_maker (lazy,
  hunter layer), logger.
- **CONNECTS TO:** dataset_factory (build_samples + samples_to_frame),
  benchmark (SampleFactory per schema), schema_v2/schema_v2_incremental.
  Uses the CANONICAL news bridge for the news context snapshot (single import
  swap replacing a verbatim row copy).

- **KEY CONCEPTS:**
  - `deterministic_sample_id` (line 42): sha256 over
    symbol|timeframe|timestamp_iso|feature_schema_id|label, prefixed "sample_".
  - SampleFactory (line 54): `feature_schema` resolved at construction
    (default "scalp_v1" — the legacy 50D contract; 60D/70D callers pass the id);
    hunter layer attached when `hunter_enabled=True` (default).
  - `news_context_at` (line 81): delegates to
    `news_bridge.news_context_at` — the causally-correct snapshot
    (events published at/before the sample timestamp only), 12-field schema
    INCLUDING categorical encodings (news_state/novelty) and per-sample
    time_since_event_sec. NEVER copies a prior row verbatim.
  - `detect_setup` (line 106): deterministic explainable rules — BREAKOUT
    (close > prior-5-bar hi + 0.5·ATR or below lo − 0.5·ATR), TREND (3+
    consecutive same-sign deltas), RANGE (spread < 0.8·ATR), else UNKNOWN.
    Uses prior rows only — causal.
  - `build_samples` (line 148): labels via TripleBarrierLabeler, parses
    timestamps (line 179: timestamp/time/datetime columns), skips unparseable
    or WAIT/unknown labels (line 187-188 — WAIT is not a neural target),
    builds feature_vector from explicit column or `feat_*` slice capped at
    schema dimension (schema-incompatible rows SKIPPED — "dropped loudly below"
    per comment, though the drop is silent: `continue` at line 200-201), news
    context, setup + hunter metadata (setup/strategy conditioned metadata),
    and the deterministic sample_id. `min_rows` default 10 (warn + empty).
  - `samples_to_frame` (line 277): serializes samples to a Polars frame:
    feat_0..feat_{n-1} + news_<field> columns + metadata columns (setup_id,
    setup_type, setup_quality, setup_tier, strategy_id, hunter_strategy_id,
    entry_decision, is_eval_sample, is_purged, label, label_str). Empty list ⇒
    empty Polars frame.

- **HOT PATH / PERFORMANCE:** Offline dataset construction. News filtering is a
  per-sample scan of the normalized news frame (news_context_at filters + sorts
  the whole frame per sample, O(samples × news_rows)) — fine for research
  datasets; the 70D builders run it once per row.

- **EDGE CASES & PITFALLS:**
  - Schema-incompatible rows (feature width ≠ schema dimension) are silently
    dropped (`continue`, line 200-201) despite the comment "dropped loudly" —
    no warning is logged at that point; a dataset with many dropped rows would
    still build with fewer samples.
  - When `feature_vector` column exists but is wrong-length, it is dropped the
    same way.
  - `detect_setup` treats prior_rows as ALL rows so far (not a fixed lookback):
    BREAKOUT/TREND/RANGE use the last 5 closes only, so memory grows but the
    rule stays causal.
  - TripleBarrierLabeler output must carry regime + atr columns for regime/
    setup; missing columns default (regime "UNKNOWN", atr 0 ⇒ UNKNOWN setup).

- **NEWS HONESTY:** No fabricated news: when `news_frame` is None or all-zero,
  the context is the documented zero vector (news off); label and sample
  identity never depend on news.
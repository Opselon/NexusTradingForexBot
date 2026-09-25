# src/nexus_scalp/news/memory/post_event.py

- PURPOSE: News memory — post-event validation + impact feedback. After a
  news event the engine compares PREDICTED IMPACT vs ACTUAL MARKET RESPONSE
  and stores the feedback, creating NEWS EXPERIENCE MEMORY: direction
  accuracy, magnitude error, time-to-response, persistence, regime
  dependency. This feedback is historical evidence consumed by future
  research/model phases; it NEVER directly modifies the production model
  (docstring).
- ARCHITECTURE LAYER: Memory/learning (advisory; no order authority).
- RESPONSIBILITY: maintain the additive news_post_event table, compute
  prediction-vs-actual metrics, aggregate accuracy summaries.
- DEPENDENCIES: NewsDatabase (db_path only — opens its own sqlite3
  connection directly), models (NewsDirection), stdlib sqlite3/uuid.
- CONNECTS TO: NewsEngine.record_market_response (feeds predicted
  direction/strength/horizon from the stored analysis + caller-supplied
  response samples); offline research/UI accuracy stats.
- KEY CONCEPTS:
  - MIN_RESPONSE_SAMPLES = 3 — fewer samples => a degenerate row with
    zeroed metrics (still stored, so the record exists).
  - `direction_correct` (line 34): BULLISH correct iff actual move > 0,
    BEARISH iff < 0; NEUTRAL/MIXED/CONFLICTED are NOT scored (return
    False).
  - `_ensure_table` (line 49): idempotent, additive creation of
    news_post_event (record_id, article_id, predicted_*, actual_move_pct,
    actual_volatility, direction_accuracy, magnitude_error,
    timing_error_sec, persistence_sec, regime, evaluated_at) + index on
    article_id. Opened via a direct sqlite3.connect to the same db_path —
    NOT through NewsDatabase (the DB layer has no post-event methods).
  - `record_response` (line 82): final_move = last sample; accuracy =
    1.0/0.0 per direction_correct; magnitude_error = |final_move| -
    predicted_strength; volatility = max(moves) - min(moves);
    timing = time to first significant move (|m| >= 0.05%);
    persistence = first-to-last sample span; regime label stored;
    INSERT OR REPLACE keyed by record_id (uuid pev_...).
  - `accuracy_summary` (line 209): aggregate direction_accuracy +
    avg |magnitude_error| over the last 200 records (None when empty).
- HOT PATH / PERFORMANCE: called at post-event evaluation only (not per
  tick); each call is one small INSERT.
- EDGE CASES & PITFALLS: samples are assumed time-ordered and
  cumulative-move — the caller must ensure ordering; a single bad article
  id still yields a row; response samples are not validated for
  monotonic timestamps; _insert failure is logged and swallowed (feedback
  loss is non-fatal by design); article_id has no FK constraint to
  news_articles (additive table).
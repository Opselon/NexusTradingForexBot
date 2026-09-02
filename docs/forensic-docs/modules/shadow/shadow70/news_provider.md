# src/nexus_scalp/shadow/shadow70/news_provider.py

- PURPOSE: Canonical NEWS-family 10D mapping for the 70D vector
  (TASK-10, INV-70D-002). Fixes the OLD live-path mapping bug: the
  previous `(nv + [0.0]*10)[:10]` was an ARBITRARY prefix slice that
  silently discarded the 12th-field state encoding (HIGH_IMPACT/ELEVATED
  flag) and time-since-event — i.e. the "News 10D" did NOT contain the
  news STATE, violating the 70D contract semantics. This module defines
  the EXPLICIT, named 10-slot projection.
- ARCHITECTURE LAYER: Domain (feature mapping, pure function).
- RESPONSIBILITY: build_news_10 (12→10 projection), verify_news_family,
  constants (NEWS_FAMILY_DIM, STATE_ENC_INDEX, TIME_SINCE_EVENT_INDEX,
  NEWS_FAMILY_SLOT_NAMES).
- DEPENDENCIES: governance.alignment (NEWS_CONTEXT_DIM=12 — the canonical
  news_context_v1 width), math.
- CONNECTS TO: 70D vector builder (fills indices 50..59), shadow70
  observe path, model_generation news vectorization conventions.
- KEY CONCEPTS — THE NAMED PROJECTION (indices into the 12-field
  news_context_v1 vector):
    idx 50: active_event_count      (src[0])
    idx 51: xauusd_relevance        (src[1])
    idx 52: usd_relevance           (src[2])
    idx 53: bullish_pressure        (src[3])
    idx 54: bearish_pressure        (src[4])
    idx 55: conflict_score          (src[5])
    idx 56: novelty_encoding        (src[6])
    idx 57: freshness               (src[7])
    idx 58: confidence              (src[8])
    idx 59: state_encoding          (src[10])   <- the previously DROPPED
                                                    decision-relevant state
  - The explicit mapping is `[src[i] for i in (0..8, STATE_ENC_INDEX)]`
    — NOT a slice. time_since_event_sec (src[11]) is deliberately
    excluded (documented omission, still available in the full 12-field
    canonical context for the model path).
  - Raises ValueError on input width != 12 — never silently pads or
    truncates (the anti-silent-reshape contract).
  - verify_news_family: exactly 10 finite values within [-3,3].
- HOT PATH / PERFORMANCE: O(10) index gather on the observation path —
    negligible.
- EDGE CASES & PITFALLS: build_news_10 accepts list or tuple but the
    generic tuple check duplicates NEWS_CONTEXT_DIM handling (a 12-tuple
    works); the state encoding values must stay in lockstep with
    governance.alignment.vectorize_news_context's state_enc map
    (NORMAL=0..BREAKING=4, STALE=5) — a change there silently changes the
    news-family semantics; drop of time_since_event means the 70D model
    cannot see recency decay (documented trade-off); pure function — no
    I/O, safe to call on the tick path.
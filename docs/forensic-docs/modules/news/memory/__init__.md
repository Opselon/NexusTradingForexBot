# src/nexus_scalp/news/memory/__init__.py

- PURPOSE: News memory package facade — store, decay, retrieval,
  feedback (post-event learning & attribution) for the PHASE 12 news
  subsystem.
- ARCHITECTURE LAYER: Package facade (memory/learning stage).
- RESPONSIBILITY: re-export PostEventValidator — the post-event
  prediction-vs-actual feedback recorder — as the package's public API.
- DEPENDENCIES: news/memory/post_event.
- CONNECTS TO: engine (imports PostEventValidator from
  nexus_scalp.news.memory), future memory consumers (research/model
  phases).
- KEY CONCEPTS:
  - The package docstring ("store, decay, retrieval, feedback") is
    aspirational in part: the current package contains ONLY
    PostEventValidator — the separate store/decay/retrieval modules
    named there do not exist; decay lives in analysis/decay.py and
    article retrieval is done via the database layer instead.
  - PostEventValidator records direction accuracy, magnitude error,
    time-to-response and persistence per news event in the additive
    news_post_event table — historical evidence that NEVER directly
    modifies the production model.
  - Post-event attribution is causal by construction (the engine only
    passes pre-decision articles), and the honest no-overlap case is
    simply "no analysis row -> no record" rather than fabricated data.
- HOT PATH / PERFORMANCE: import-time only; runtime cost lives in
  post_event.py (once per post-event evaluation, off the tick path).
- EDGE CASES & PITFALLS: __all__ is limited to PostEventValidator —
  adding future memory modules requires updating this file and __all__
  together; the docstring's module list should be kept truthful as the
  package evolves.
- NOTE: memory data is evidence for future research — it must never be
  fed back into the live model without an explicit research gate.

- RELATED ARTIFACTS:
  - src/nexus_scalp/news/memory/post_event.py — the only implemented
    memory module; owns the news_post_event table.
  - src/nexus_scalp/news/database.py — the shared news.db the table
    lives in.
- REVISION NOTES: memory is deliberately read-only evidence for research
  phases; the engine's record_market_response is the single entry point
  for new feedback rows.

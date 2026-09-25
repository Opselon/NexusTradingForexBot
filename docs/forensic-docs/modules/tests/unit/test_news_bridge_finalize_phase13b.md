# tests/unit/test_news_bridge_finalize_phase13b.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- PHASE 13B news bridge FINALIZATION suite (spec 26 extensions): real SQLite news DB export, no-news-db CLI contract, schema invariants.
- Real DB export: multiple sources + JSON impacts merge correctly; INVALID JSON impacts do not corrupt the frame (typed safe defaults); missing/malformed values default safely; categorical fields encoded deterministically; empty DB export → empty frame.
- No-news-DB CLI contract: no news frame → zero context + warning path, dataset identity changes; real news dataset changes `dataset_id` deterministically (content-addressed identity).
- Schema invariant: exactly the 12 news fixture fields, ordered; no dead-zero columns.
- 27 defs / 511 lines; real SQLite seeded via `_seed_db`, reads through `_queue.join()`-style flush fixtures.
- NOTE: pairs with test_news_bridge_phase13b.py + test_news_bridge_contract_phase13b.py (same module, three suites — user naming preference).
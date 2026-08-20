# tests/unit/test_news_keywords_dataset.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- PHASE 12 expansion: news keyword-analysis dataset — size/category distribution/determinism, keyword definition validity (direction bias, topics, weight bounds), corpus coverage analytics.
- Dataset guards: large + deterministic; categories cover all major groups; keyword definitions valid; directional keywords include bullish AND bearish; `get_keyword("NOT_A_KEYWORD_XYZ") is None` (unknown → None, no fabrication).
- Coverage: empty corpus → SAFE summary (no crash); hits/share computed; direction distribution reflects declared bias; top keywords sorted by hits; `limit_texts` bounded.
- Per-article hits: article hit keys restricted to the declared lexicon.
- 32 defs / 373 lines; pure data tests.
- NOTE: keyword lexicon lives with the news module (lookup via `get_keyword`); pattern-performance concerns are covered in the skills' forensics, not here.
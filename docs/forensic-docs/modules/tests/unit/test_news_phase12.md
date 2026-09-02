# tests/unit/test_news_phase12.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- PHASE 12 News Intelligence Engine behavioral suite (93 defs / 1169 lines): every test asserts OBSERVABLE behavior — rows persisted, duplicates actually collapse, decay actually reduces, rate limit actually falls back, conflicts actually resolve.
- Ingestion: canonicalize item → content+title hashes and UTC timestamps; same content → same hash; updated article → DIFFERENT hash (`test_03_updated_article_new_content_different_hash`); malformed feed → typed failure; rate-limit result flag `rate_limited and retry_after_sec == 60`; scheduler due logic; source disablement.
- Dedup: exact duplicate collapses; syndicated title merges evidence; title window matching (`find_duplicate_title(...) is None` outside window); normalized title strips noise.
- Decay: `decay.is_stale(now - timedelta(hours=2), now)` — staleness actually reduces.
- Rate limit / queue: worker `_queued_ids` observed; queued-writer flush (`_queue.join()`) before row reads — never `close()` mid-test (BUG-058).
- SQLite DB fixtures (`news_db`, `seeded_db`); no MT5/network.
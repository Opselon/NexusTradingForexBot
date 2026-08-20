# tests/unit/test_research_registry_null_score_bug075.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- BUG-075 regression suite: research registry NULL-score crash + stale web bundle detection (2026-08-18 forensics).
- Verified defects covered: (1) strategy rows crash the registry when `score` is a JSON-null literal; (2) JSON writer emitted bare `null`; (3) stale web bundle shipped.
- Guards: absent score becomes EMPTY OBJECT in JSON, never a `null` literal; valid scores preserved; `row_safe` normalizes null literals and keeps valid JSON; `registry_from_row` TOLERATES null score (`entry.score is None` — no crash).
- Web bundle: `verify_web_assets` PASSES when hashes match, FAILS on stale bundle; build script records web hashes; `test_repo_web_bundle_is_current` — repo's committed bundle must match built assets (freshness gate for the Web/ tree).
- 11 defs / 247 lines.
# NSE Swarm Run — 2026-09-11 ~15:20 UTC — Role 5 (Data/Ingestion)

**Job:** NSE Autonomous Bug-Fix Swarm (2530c1ff54a0) | **HEAD at start:** 07d83fc1 (all 11 required contexts green)

## Defect: BUG-258 — news DB list caps silently truncated explicit caller limits (newest-500 slicing)

**Severity:** HIGH (data-integrity, research money path) | **Status:** FIXED-VERIFIED (local; PR path pending — direct main push blocked by branch protection)

### Root cause (file:line at 07d83fc1)

- `src/nexus_scalp/news/db_analysis.py:152` — `list_analysis()`: `bounded = max(1, min(int(limit), 500))`.
- `src/nexus_scalp/news/db_articles.py:234` — `list_articles()`: same silent 500 clamp.
- `src/nexus_scalp/model_generation/news_bridge.py:308` — `build_news_frame_from_db()` passed `limit=2000` and assumed it was honored.
- Net effect: any caller requesting >500 rows ran on a silently narrowed NEWEST-500 slice. No warning, no error, no contract note.

### Why it matters (money path)

`build_news_frame_from_db` is THE bridge from the news DB to dataset generation (the documented >=1y news-aware retrain prerequisite, Agent-3 wave-2 forensics §6.2):

1. **Oldest history silently dropped:** with >500 analyzed articles the export keeps only the newest 500 (ordered `analyzed_at DESC`), so news context for older bars silently became zero — the retrain would train on a silently-truncated news history and produce a confidently wrong artifact.
2. **Windowed exports lose rows INSIDE the window:** the newest-500 slice is applied BEFORE the start/end filter, so a requested window can be missing its oldest rows. Executable probe (505 stored analyses spread 2026-08-10..14): requesting the 08-10..08-12 window (300 stored rows) exported **295** — 5 rows lost from inside the requested window.
3. Same silent clamp narrowed `pro_auto.run_pro_cycle`'s gold-first drain pool (2000) and `ai_service.auto_prune_irrelevant` (2000) to 500.

### Fix (minimal, additive)

- `db_analysis.py`: `ANALYSIS_LIST_HARD_CAP = 20_000` + `_bounded_limit()` — explicit caller limits honored up to the hard cap; over-cap truncation is newest-first and logs exactly one `LIST_LIMIT_CLAMPED` WARNING. Applied to `list_analysis` + `list_ai_analysis`.
- `db_articles.py`: `ARTICLES_LIST_HARD_CAP` + `_bounded_article_limit()` (same contract) for `list_articles`.
- `news_bridge.py`: `build_news_frame_from_db(limit=None)` — default exports EVERY analysis in the requested window (the DB hard cap still bounds memory); explicit `limit` still honored for diagnostic/preview exports. Import is lazy-safe (`nexus_scalp.news.db_analysis` module constant read at call time).
- Hard-cap values (20k rows ≈ a few MB of dicts) keep the original memory-bound intent of the old clamp without the silent narrowing.

### Regression net

NEW `tests/unit/test_news_db_truncation_honesty.py` (10 tests, deterministic UTC timestamps, module-logger monkeypatch per BUG-112/118, no wall clock):

- caller limit 2000 honored (analysis + articles), small/default requests unchanged;
- monkeypatched hard cap truncates newest-first; exactly ONE warning on clamping calls, zero on honest calls;
- `build_news_frame_from_db`: full window export complete (505/505), window filter still applies (300/300 — was 295 pre-fix), default unbounded.

Gated into `tests/critical_suite.txt` (path exists at HEAD before push; same-commit add per the rc=5 trap).

### Verification evidence

- RED: 9/10 failed pre-fix at 07d83fc1 (probe output preserved in this report + console transcript: `stored=505 exported=500`, `window stored=300 exported=295`).
- GREEN post-fix: 10/10 new tests + **192 passed** across the 10-suite news lane (bridge contract/finalize/phase13b/phase12/bug197/bug217/budget/pro-auto-console/keywords + new net), 34.68s.
- ruff check + format clean on all 4 touched files; mypy clean on all 3 src files (Slim venv `.venv-linux`, `PYTHONPATH=src:.`).
- Post-fix re-probe: window export 300/300, full export 505/505.

### Constraints

No workflow files, no gateway adapter, no frozen models, no tick-pipeline change. News DB read paths only — write paths untouched. INV-001 untouched (no hot path: dataset build is offline).

## Next run plan

- Push via PR branch (branch protection: 11 required checks, no direct main push). PR #130 path: branch `swarm/bug258-news-list-truncation`.
- UPDATE-SIG-OPERATOR still parked on operator (NSE_UPDATE_SIGNING_KEY secret).
- Watch: G3 scoring.py:416 wall-clock fallback (`datetime.now()` vs threaded `now`) — the `_evaluate_minimum_loss_optimization` seam was fixed by TASK-AUDREV-G3-HOLD-CLOCK, but `_calculate_hold_value_score` (scoring.py:413-418) still derives holding_duration from the host wall clock when called from `_evaluate_hold_score` (no `now` threaded). Candidate for next cycle.

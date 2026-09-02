# src/nexus_scalp/forensics/news_sources.py

- PURPOSE: News source forensic classifier (TASK-12 §13-15) — per-source
  classification BEYOND HTTP status. A source that technically returns
  HTTP 200 but produces 0 usable articles is NOT healthy (§13/§25 of
  TASK-11). Read-only.
- ARCHITECTURE LAYER: Application (forensics analysis).
- RESPONSIBILITY: classify_source (per-source taxonomy),
  analyze_news_sources (runs classification over live news.db),
  STALE_THRESHOLD_SEC / EMPTY_ARTICLE_THRESHOLD / CLASSIFICATIONS.
- DEPENDENCIES: sqlite3 (RO URI), datetime, logging.
- CONNECTS TO: news health dashboard/API, forensics checks
  (check_news_health), periodic telegraphic reporting.
- KEY CONCEPTS — THE §14 TAXONOMY:
  HEALTHY / HTTP_SUCCESS_EMPTY / HTTP_SUCCESS_INVALID /
  HTTP_SUCCESS_STALE / HTTP_SUCCESS_WRONG_SCHEMA /
  HTTP_SUCCESS_DUPLICATE / HTTP_FAILURE / UNKNOWN (+ DISABLED for
  disabled sources).
  - classify_source decision order:
    1. disabled → DISABLED.
    2. consecutive_failures > 0 and last_status None (never succeeded) →
       HTTP_FAILURE.
    3. consecutive_failures > 0 and last_status not in (200, 304) and
       article_count == 0 → HTTP_FAILURE.
    4. last_status == 200 and article_count == 0 → the 200-but-wrong
       family: parse_failure_count → HTTP_SUCCESS_INVALID;
       duplicate_count → HTTP_SUCCESS_DUPLICATE; else
       HTTP_SUCCESS_EMPTY.
    5. article_count > 0 and success_age > stale_window (max(24h,
       poll_interval*3)) → HTTP_SUCCESS_STALE.
    6. healthy_flag AND articles AND success_age <= window → HEALTHY.
    7. healthy_flag and articles (freshness unknown) → HEALTHY
       (lenient fallback).
    8. else UNKNOWN (insufficient evidence — never PASS).
  - Stale window is PER-SOURCE: max(24h, poll_interval_sec * 3) —
    low-cadence official sources (press releases) get a longer window.
  - HTTP_SUCCESS_WRONG_SCHEMA is in the taxonomy but is never emitted
    by this classifier (no schema field is inspected — see pitfalls).
  - analyze_news_sources: joins news_sources with news_health +
    per-source article counts; emits per-source classification with
    evidence + summary counts; degraded_count = anything not
    HEALTHY/DISABLED/UNKNOWN.
- HOT PATH / PERFORMANCE: report/inspection cadence; O(sources +
  articles group-by).
- EDGE CASES & PITFALLS: WRONG_SCHEMA classification exists in
  CLASSIFICATIONS but NO branch produces it (unreachable taxonomy
  member — the parser-schema signal is not wired in);
  healthy_flag=1 with 0 articles and no status silently falls to
  UNKNOWN, not HEALTHY; 304 (not-modified) counts as success-like in the
  failure gate (failure only when status NOT in (200,304) AND 0
  articles); duplicate_count is only consulted inside the 0-article
  branch — a source with articles but all duplicates is classified
  HEALTHY/STALE, not DUPLICATE; analyze_news_sources returns
  available=False + empty when news.db lacks the news_sources table
  (no distinction from missing DB).
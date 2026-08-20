# src/nexus_scalp/news/analysis/decay.py

- PURPOSE: News impact time decay — new information matters more; impact
  MUST decay with age, with DISTINCT decay classes per horizon (BREAKING
  minutes / MACRO hours / POLICY hours-days / STRUCTURAL days-weeks).
  One fixed decay for every news type is explicitly forbidden
  (docstring).
- ARCHITECTURE LAYER: Domain/analysis logic (pure math engine).
- RESPONSIBILITY: freshness(t) = 0.5 ** (age_sec / half_life_sec);
  decayed strength; staleness thresholds — configurable and testable
  (NewsDecayConfig).
- DEPENDENCIES: NewsDecayConfig, models (NewsImpactHorizon); stdlib
  math/datetime.
- CONNECTS TO: NewsContextCache.build (per-event freshness weighting),
  NewsAnalysisPipeline construction, staleness decisions in context
  (stale_after_sec).
- KEY CONCEPTS:
  - Default half-lives (line 23): BREAKING 15min, MACRO 4h, POLICY 24h,
    STRUCTURAL 5d — a central-bank regime change stays relevant far
    longer than a minor headline.
  - `_build_half_lives` (line 38): converts config units (minutes/hours/
    days) into seconds once at construction.
  - `half_life_sec` (line 47): unknown horizon falls back to MACRO —
    safe default, never a crash.
  - `freshness` (line 50): exponential half-life decay rounded to 4dp;
    negative age (future-dated event) clamped to 0.0, so a future-dated
    article carries freshness 1.0 during its lead-in; half_life <= 0
    returns 0.0 (defensive).
  - `decayed_strength` (line 65): strength * freshness — the standard
    application for impact-at-time-t.
  - `is_stale` (line 75): raw age > stale_after_sec (config default
    3600s) — a distinct notion from freshness: a STRUCTURAL event can
    still be "fresh" by half-life yet stale by wall-clock policy.
  - `_as_utc`: naive datetimes are assumed UTC (tagged, not offset-
    adjusted).
- HOT PATH / PERFORMANCE: two float ops per call; invoked once per
  article per context build in the worker — never on the tick path.
- EDGE CASES & PITFALLS: rounding to 4dp means freshness resolves to
  0.0 below ~0.00005; context drops events only when freshness <= 0.02;
  is_stale does NOT consult half-life — callers must pick the right
  staleness notion per use case.
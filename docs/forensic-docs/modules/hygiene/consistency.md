# src/nexus_scalp/hygiene/consistency.py

- PURPOSE: Data Consistency Rules (TASK-22) — read-only validation rules
  per domain (spec §12). Each rule returns a structured finding
  (PASS / VIOLATION / NOT_APPLICABLE) with evidence (count + first N
  offenders). The engine NEVER mutates — violations are reported; uncertain
  rows go to quarantine via the CALLER.
- ARCHITECTURE LAYER: Application (read-only validation).
- RESPONSIBILITY: ConsistencyRuleEngine (per-db rule scans), _as_ts
  timestamp parser, findings_summary/findings_json reporting,
  FEATURE_DIMENSIONS registry.
- DEPENDENCIES: sqlite3, json, dataclasses, datetime. No writes.
- CONNECTS TO: hygiene worker/planner (evidence collection), report,
  quarantine (caller decision), web diagnostics.
- KEY CONCEPTS:
  - Rules, all defensive (try/except per check — a missing column marks
    the check NOT_APPLICABLE, never a crash):
    - TRADE-001: closed rows with close_time < open_time (string compare
      on ISO columns).
    - TRADE-002: volume NULL or ≤ 0.
    - TRADE-003: empty/whitespace symbol.
    - LEDGER-001: CLOSED rows with NULL or NaN pnl (`pnl != pnl`).
    - UNREAL-001: PENDING/OPENED rows older than 14 days (abandoned
      states).
    - DATASET-001: experiences with unparseable/impossible
      decision_timestamp (sample 400 rows).
    - DATASET-002: outcome rows missing idempotency_key (broken label
      linkage).
    - DATASET-003: candle feature-column count must be in the declared
      schema set {50, 60, 70} (meta columns excluded), else VIOLATION.
    - DATASET-004: candle ts/timestamp sanity.
    - NEWS-001: published_at sanity (sample 500).
  - _as_ts (line 79): accepts ISO strings ("Z"→+00:00, 10-char dates
    become midnight UTC) and epoch numerics (values ≥1e12 treated as
    MICROseconds/1e6 — note: NOT ms/1e3 like other modules); sanity bounds
    MIN_TS_EPOCH 2000-01-01 .. MAX_TS_EPOCH +5y.
  - FEATURE_DIMENSIONS (line 46): scalp_v1 50 / scalp_v2 60 / scalp_v3 70
    / scalp_v4 70 / scalp_liquidity_v1 60 — mirrors features/schema_contract;
    only used when the runtime does not expose a schema; dataclass allows
    override injection.
  - _mk (line 527): offenders capped at 10; tuple rows become col0/col1…
    dicts; status auto PASS/VIOLATION from evidence, or forced
    NOT_APPLICABLE.
  - findings_summary (line 557): PASS/VIOLATION/NOT_APPLICABLE counts +
    violation details (spec §12 report shape).
- HOT PATH / PERFORMANCE: rule queries are LIMIT 25/400/500 bounded;
    feature-count rules use PRAGMA table_info + column-count arithmetic
    (no per-row scans); runs only during hygiene cycles.
- EDGE CASES & PITFALLS: _as_ts uses microseconds for ≥1e12 while
  incidents worker._to_telemetry uses MILLISECONDS for ≥1e12 — a
  1.7e12 ms epoch stored in a DB column would parse as year 1970+something
  wrong (microsecond interpretation of millisecond values); UNREAL-001's
  cutoff uses string ISO compare — mixed timestamp formats miscompare;
  DATASET-003 table scan misses an actual per-row width check (schema-
  level only, and a table with ≥5 feature cols and width 71 flags even if
  the runtime legitimately extends the vector); TRADE-001 compares
  close_time < open_time as TEXT (fine for ISO-8601).
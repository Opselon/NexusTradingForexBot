# src/nexus_scalp/forensics/checks.py

- PURPOSE: Centralized forensic health checks (TASK-11 POST-70D
  monitoring). Every check is a small, READ-ONLY, failure-isolated
  function producing a CheckResult with the five-level vocabulary. No
  check mutates production databases/artifacts/runtime state; NO check
  auto-repairs — it detects, classifies and reports (§0/§55).
- ARCHITECTURE LAYER: Application (check functions).
- RESPONSIBILITY: ~30 check functions + shared helpers grouped by
  invariant family.
- DEPENDENCIES: forensics.models, forensics.references, sqlite3,
  math, time, pathlib; lazy imports (torch/numpy/feature producers/
  config/settings) inside functions; strict read-only sqlite URIs
  (_ro_connect ?mode=ro).
- CONNECTS TO: ForensicHealthEngine.check_groups (the wiring matrix),
  deploy gate, telegram report, dashboard.
- KEY CONCEPTS — CHECK INVENTORY (id → what it verifies):
  - FeatureContract: CHECK-FCS-00 (all registered schemas dimension >=
    50/Base prefix preserved), CHECK-FCS-01 (a 70D schema registered,
    else UNKNOWN "series blocked" — never fabricates), CHECK-FCS-04
    (actual vector length 50/60/70, finite, within [-3,+3]), CHECK-FCS-03
    (liquidity family layout; 60D→indices 50..59 candidate; 70D→60..69;
    index-60 name must be bsl_distance_atr; requires FROZEN liquidity
    references else UNKNOWN).
  - Model: CHECK-MDL-01 (champion model.pt + model.scaler.npz present;
    scaler missing → CRITICAL), CHECK-MDL-03 (state-dict input width ==
    active schema dimension; mismatch → CRITICAL MODEL_SCHEMA_MISMATCH).
  - Parity: CHECK-RTP-03 (CAUSAL CANARY — builds 60 deterministic bars,
    computes the vector twice with the SAME completed history but
    different FORMING ticks; bar-derived feature set (indices
    0..49 minus tick-derived) must be unchanged — future leakage →
    CRITICAL FUTURE_LEAKAGE; tick-derived set must react (positive
    control) else UNKNOWN), CHECK-RTP-01 (training producer
    compute_60d_extras + live producer ScalpFeatureEngine — combined
    dim check; PARITY_BROKEN → CRITICAL).
  - Dataset: CHECK-DTA-01 (datasets/<id> presence; absent → UNKNOWN;
    feature_count < 50 → CRITICAL DATASET_SCHEMA_DRIFT).
  - Accounting: CHECK-ACC-01 (broker vs ledger PnL sums; tolerance =
    |broker|*0.02+5; divergence → WARNING ACCOUNTING_DIVERGENCE, never
    auto-rewrite), CHECK-ACC-02 (one canonical outcome per execution
    identity; legacy BUG-097 dup known set {"152494870397"} → WARNING
    historical; any OTHER dup → CRITICAL DUPLICATE_ECONOMIC_OUTCOME),
    CHECK-ACC-03 (MFE >= 0 and MAE <= 0 invariant (BUG-096); violations
    closed on/after 2026-08-19 fix date → CRITICAL, before → WARNING
    immutable historical), CHECK-ACC-04 (experience outcome gap — uses
    analyze_experience_gap defect_rate; TASK-12 §16-20 correction:
    only EXECUTED trades with missing outcomes indicate a defect;
    never-traded decision samples are legitimate).
  - Database: CHECK-INT-01 (PRAGMA integrity_check ok on audit/news/
    candle_intel; corruption → CRITICAL; missing → UNKNOWN),
    CHECK-MIG-01 (schema_version vs expected; pending migrations →
    WARNING MIGRATION_PENDING; below expected with NO pending →
    CRITICAL MIGRATION_DRIFT), CHECK-GRW-01 (DB sizes vs 2026-08-19
    baseline {audit 50.9MB, news 6.4MB, candle 1.1MB}; >3x or <0.3x
    (above 5MB floor) → WARNING DB_GROWTH_ANOMALY).
  - Liquidity: CHECK-LIQ-01 (per-index 60..69 stats vs FROZEN
    references: FEATURE_DEAD (same-value >= 99%, near-zero variance,
    100% missing, constant clipping) → DEGRADED; FEATURE_FLOOD (mode >=
    90% & std < ref*0.1, or near-clip-bound & std < ref*0.05) →
    DEGRADED; mean drift z > 5 → CRITICAL, > 3 → WARNING, > 2 → WATCH;
    zero-rate/missingness > ref + 0.1 → WARNING).
  - News: CHECK-NWS-01 (enabled sources all healthy — a 200-but-0-
    articles source is NOT healthy (§25); 0 articles → DEGRADED
    NEWS_NO_DATA; 0 consensus → WARNING NEWS_PARSER_INERT),
    CHECK-NWS-02 (worker checkpoint: 0 cycles → DEGRADED; last cycle >
    24h → DEGRADED WORKER_STALLED), CHECK-NWS-03 (availability matrix:
    News/Liquidity ON/OFF → unambiguous 50D/60D/70D contract; enabled
    family missing its DB or frozen references → CRITICAL
    FEATURE_CONTRACT_INCOMPLETE).
  - Shadow: CHECK-SHD-01 (shadow never attached → UNKNOWN; RUNNING but 0
    comparisons → DEGRADED SHADOW_NO_PROGRESS; errors with 0
    comparisons → WARNING SHADOW_ERRORS_SILENT).
  - Governance: CHECK-GOV-01 (impossible lifecycle combos across BOTH
    registries — REJECTED+CHAMPION, not-approved+CHAMPION → CRITICAL;
    multiple champion fingerprints → DEGRADED), CHECK-GOV-02 (champion
    identity: current registry fingerprint vs disk hash; mismatch →
    CRITICAL CHAMPION_IDENTITY_MISMATCH; stale extra fingerprints →
    DEGRADED).
  - UI/API/Telegram/Trace/Workers/Runtime/Performance:
    CHECK-UI-01 (canonical /api/live/state endpoint), CHECK-UI-02
    (Web bundle version markers — absent → UNKNOWN, honest),
    CHECK-API-01 (semantic-health endpoint existence in server source),
    CHECK-API-02 (chart payload: 0 bars → DEGRADED CHART_DATA_DEGRADED;
    OHLC violations/dupes → DEGRADED), CHECK-TEL-01 (telegram config/
    enabled/worker health), CHECK-TRC-01 (worker-state tables present),
    CHECK-TRC-02 (correlation_id/checksum columns), CHECK-TRC-03 (log
    scan for silent-fallback patterns → WARNING SILENT_FALLBACK_CANDIDATE),
    CHECK-RSW-01 (research/intelligence worker cycle progress),
    CHECK-RTM-01 (config mode readable; operational mode verified at
    runtime only), CHECK-PER-01 (cheap migration-registry resolve timing),
    CHECK-GRW-02 (telegram queue size >= 80 → WARNING QUEUE_GROWTH).
  - Helpers: _safe (raised check → UNKNOWN CHECK-RAISED), _row_count,
    _iso_age_seconds, _champion_artifact_info (config-driven path +
    well-known candidates), _integrity_for (PRAGMA integrity_check,
    journal_mode, foreign_keys, WAL size, unexpected tables),
    _broker_ledger_divergence, _news_state, _shadow_state,
    _load_runtime_config.
- HOT PATH / PERFORMANCE: checks run on snapshot cadence; the causal
  canary and parity canary build 60-bar fixtures and run real producers
  (heaviest checks, ~ms); bounded result sizes (violations[:20]);
  DB reads strict RO.
- EDGE CASES & PITFALLS:
  - check_migration_state line 1281: pending calc `base +
    reg.index(m) + 1 > applied` is a HEURISTIC (index-based version
    arithmetic) — fragile if migration ids are not sequential.
  - _champion_artifact_info hardcodes the well-known path
    artifacts/models/scalp/XAUUSD/v1.0.0/model.pt — product-specific.
  - CHECK-RTP-01 computes extras from 5 hardcoded OHLCV rows while
    CHECK-RTP-03 uses 60 bars — different fixtures, fine but noted.
  - Line 494 `CAUSAL_FIXTURE["bars"]` is a no-op deref (leftover); the
    fixture rows are never used — the canary builds bars programmatically.
  - check_duplicate_economic_outcome: relies on "execution_id" column
    presence; falls back order_id/ticket — a schema without any of them
    silently passes (no dup detection).
  - CHECK-ACC-01 tolerance formula treats None broker/ledger as within
    tolerance (tolerance=0.0) → PASS when sums are absent.
  - Fetch of `(st.get("worker_state") or [{}])[0]` and
    `(st.get("latest_runtime_health") or [{}])[0]` — empty state degrades
    silently to {} then specific checks handle.
  - check_runtime_mode_integrity always returns PASS with
    "operational_mode UNKNOWN" — the §40 operational check is
    effectively a stub (config-only).
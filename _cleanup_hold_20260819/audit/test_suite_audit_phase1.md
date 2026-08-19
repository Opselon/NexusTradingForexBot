# NexusTradingForexBot — Test Suite Audit (Phase 1: AUDIT-ONLY)

Date: 2026-08-19 · Auditor: Hermes (test-suite-consolidation)

## 1. INVENTORY (pytest collection, --ignore=playwright_e2e)

| Metric | Count |
|---|---|
| Test files (unit) | 117 |
| Test files (integration) | 13 |
| Test files (release) | 2 |
| Test helpers/fixtures | 4 (helpers) + fixtures/ |
| TOTAL discovered test cases | **2,308** (unit 2,184 / integration 98 / release 26) |
| Parameterized nodes | embedded in per-file counts |
| Skipped (baseline) | 6 (perf probes, CUDA-absent, no-release-dir, no-real-positions) |
| Currently failing (baseline full run) | 10 (see §4) |
| Collection error | 1 (test_playwright_e2e — playwright not installed locally; expected) |

Full-run: 2,308 tests, 0 errors, 10 failures, 6 skipped, 1,170 s (19.5 min serial).

## 2. MAPPING: TESTS → PRODUCTION AREAS (major groups)

- **Risk** — test_risk_engine.py (8 tests/30 asserts: kill-switch, dynamic matrix, SL scaling,
  equity scaling, invariance, free margin, safety/boundaries, no-flat-2-lot regression) — Tier 1.
- **PnL/Accounting** — test_accounting_core.py (65/176), test_accounting_advanced_metrics.py (8/43),
  test_accounting_hedging.py (2/18), integration test_accounting_api.py (15/81) — Tier 1.
- **Execution/Orders** — test_order_manager*.py, test_order_lifecycle.py, test_trade_lifecycle_task3.py
  (26/74), test_execution_architecture.py — Tier 1.
- **Strategy/Intelligence** — test_strategies_seeder_phase15c.py (4/14), test_strategies_ichimili_phase15c.py
  (12/28), test_intelligence_phase09.py (19/48), test_behavior_anomaly_intelligence_phase16.py (26/55),
  test_experience_intelligence.py (54/217), test_performance_* (33+29) — Tier 1/2.
- **ML/Lifecycle** — 39 files/897 tests (model_generation 95, governance 60, lifecycle 44, 70d suites,
  liquidity…). Tiers (ML): T1 598, T2 269, T3 26, T4 4.
- **Research/Backtest/OOS** — test_research_phase09b.py (45), test_research_task4_*.py, walk-forward,
  shadow70, temporal — Tier 1 (leakage/prevention + OOS).
- **Persistence/Infra** — 29 files/581 tests audited (migrations 38, hygiene 37, mt5 33+1, releases,
  news 66, telegram, debug snapshot…). Tiers: T1 227, T2 344, T3 8, T4 2.
- **Release/Observability** — 20 files/464 tests: T1 345, T2 106, T3 12, T4 1.

Total classified so far: **1,942 tests (84% of suite)** — T1 1,170 · T2 719 · T3 46 · T4 7.

## 3. CLASSIFICATION SUMMARY (per §6 tiers)

- Tier 1 (must preserve): ~1,170+ — risk, accounting, execution, contracts, DB integrity, regressions.
- Tier 2 (important): ~719+ — services/adapters/infra.
- Tier 3 (redundant/overlapping): 46 — consolidation candidates.
- Tier 4 (obsolete/dead): 7 — quarantine candidates (high-confidence only).

## 4. EXECUTION ANALYSIS (full suite, read-only)

Failures (10) triaged:
1. BUG-118 tests ×3 (test_model_lifecycle_phase10) — PASS in isolation → test-order contamination (shared
   class state / capsys). Protected (BUG-118 regression), left as-is.
2. test_hardened_protocol::test_model_rollback_on_health_check_failure — shared `wf_candidate` artifact
   collision: 70D scalp_v4 test writes 70D scaler, 50D default test reads it → dim mismatch.
   PASSES after removing the stray 70D artifacts. TEST-ISOLATION defect (shared default path), not prod.
3. test_frontend_assets_phase14::test_all_getelementbyid_refs_exist — app.js still references 8 REMOVED
   debug-model DOM ids (dead code from the old diagnostics grid, superseded by debug-runtime-grid in
   987c550). Genuine product dead-code → test correctly catches it. Needs product fix (later), test preserved.
4. test_post70d_monitoring_activation::test_http_200_with_articles_healthy — date-dependent: hardcoded
   2026-08-18 timestamp is now >24h stale → production correctly returns HTTP_SUCCESS_STALE. TEST FLAKY-BY-DATE,
   not prod. (Fix: use `datetime.now(UTC) - timedelta(minutes=…)`.)
5. test_web_security::test_06_server_log_contains_detailed_exception — logging-capture brittleness
   (order-dependent root-handler surgery; known structlog trap documented in repo).
6. test_mt5_accounting_api_contract::test_period_report_has_real_financials — environment (needs live MT5
   account data).
7. test_build_script_hardening::test_cli_help_strings_are_ascii_safe — em-dash help strings added AFTER
   the ASCII-safety test (authoring order); legitimate newer help text conflicts with static ascii scan.
   Test-policy tension, not prod defect.
8. test_release_hardening::test_verifier_fails_on_tampered_release — fixture Web assets can't hash-match
   repo Web source (STALE WEB BUNDLE check) → fixture needs updating.

## 5. DUPLICATION FINDINGS

Structural scan (normalized-body) found only 3 EXACT duplicates repo-wide (body hash identical):
- test_phase08_experience_intact (test_model_lifecycle_phase10.py + test_research_phase09b.py) — exact body
- test_accounting_intact (test_model_lifecycle_phase10.py + test_shadow_phase11.py) — exact body
- test_1/2_insufficient_h{1,h4}_history (test_htf_warmup_gate.py) — identical except hN count → parameterize

Sub-suite findings: 41 near-duplicate/overlap groups (news_bridge family 7, telegram 4-file family,
migration idempotency 3-file family, integration pipeline pair, settings telegram dual-path, etc.).
Most are LAYERED (unit vs observability vs template) — consolidate cautiously, keep cross-validating asserts.

## 6. WEAK / OBSOLETE CANDIDATES (high-confidence, for Phase-2 quarantine)

1. test_model_generation_phase13.py::test_48/49/50/51_phase08..11_imports_intact — `assert True` import
   smokes, duplicated by dedicated phase suites → T4 OBSOLETE (4 tests).
2. test_model_generation_phase13.py::test_42_challenger_cannot_execute_mt5 — tautology
   (`assert "mt5" not in src or "mt5_port" not in src` short-circuits) → weak, needs rewrite not removal.
3. test_model_generation_phase13.py::test_mg28_challenger_shadow_no_orders — `... or True` tautology → weak.
4. test_order_manager_audit.py (2 tests, 1 assert each: legacy-file deletion scan + import smoke) → weak.
5. test_domain_models.py (3 tests/59 lines, 0.67 asserts/test) — thin construction-only checks → weak.
6. test_database_migrations_phase18.py::test_cli_uses_same_engine/test_startup_uses_same_engine —
   hasattr smokes superseded by real CLI tests (T4).
7. test_logging.py::test_configure_logging_and_get_logger — trivial smoke (1 assert).

## 7. FLAKY / SLOW

- Flaky-by-date: test_post70d_09_http_200_with_articles_healthy (fixed timestamp).
- Order-dependent: BUG-118 trio; web_security test_06 (logging capture); hardened_protocol rollback
  (shared wf_candidate path).
- Slow: test_bug106_15_20k_benchmark (104s), test_api_news_refresh_bounded (93s), release cli exit codes
  (22s). Not deletion candidates (perf/regression value).

## 8. RISK ASSESSMENT (proposed Phase-2 changes)

- Quarantine T4 import-smokes (4+2+2+1): risk LOW — dedicated phase suites + full-suite import coverage exist.
- Consolidate exact dups (2 cross-file + 1 intra-file param): risk LOW — identical bodies, keep one per home.
- Rewrite (not remove) tautologies (2): risk LOW-MED — current asserts are no-ops; rewrites strengthen.
- DO NOT touch: BUG-118 trio, news_bridge family, BUG-054 payload pin, BUG-082 chart, release hardening
  (authoring-order issues belong to product fixes, not test removal).

## 9. RECOMMENDED FINAL STRUCTURE

- Keep ~2,290 tests (remove ~12-18 via quarantine + ~8 via merge/param), preserve all Tier 1/2.
- Fix authoring issues: date-flaky timestamp, wf_candidate tmp_path isolation, ascii-help test update,
  stale Web fixture — as TEST fixes (not product changes), owned by respective bug owners.
- The 10 failures are NOT production regressions; 3 pass in isolation, 2 pass after env cleanup,
  2 are env-dependent, 2 are authoring-order, 1 is genuine dead-code detection.

## 10. PROTECTED FILES (parallel-agent work — untouched)

- tests/unit/test_model_lifecycle_phase10.py (BUG-118 tests by Hermes-Forensic-03; clean at HEAD but
  semantics pending)
- tests/conftest.py (shadow70 fixture registration by TASK-06)
- Web/app.js + Web/index.html (in-flight UI work; failure #3 is a product-dead-code issue for the UI owner)
- agents/bugs.md, agents/skill.md, scratch/* (parallel agents)
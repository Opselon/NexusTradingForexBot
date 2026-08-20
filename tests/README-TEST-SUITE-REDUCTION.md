# TEST SUITE REDUCTION — CRITICAL SUITE GUIDE (2026-08-20, Hermes-TestReduction)
#
# WHY
# ===
# The repo previously shipped 2335 tests / ~131 files. Most were low-value:
# repeated mock-verification, cosmetic formatting checks, DTO property trivia,
# duplicated integration contracts, parameterized explosions, and snapshot
# style tests that protected implementation detail instead of business risk.
# The whole suite took ~22 minutes and bloated every CI run.
#
# THE NEW CONTRACT
# ================
# 1. The DEFAULT CI gate (quality job) now runs the CRITICAL SUITE
#    (tests/critical_suite.txt — the manifest) instead of `pytest tests/unit/`.
# 2. The Critical suite is the small, high-signal regression net:
#      MODEL * TRAINING * CHAMPION/SHADOW * ACCURACY * FEATURE/DATA CONTRACT *
#      ACCOUNTING * RISK * EXECUTION * BACKTEST * WALK-FORWARD * OOS *
#      STRATEGY FACTORY * WHOLE-APPLICATION CYCLE
#    It must stay green on every push.
# 3. `tests/unit/test_critical_suite.py` is the heartbeat: a single test
#    walks the complete chain with REAL components —
#      tick -> feature vector (50D) -> ScalpNet probabilities -> SignalPolicy
#      -> RiskEngine sizing -> TradeOrder execution audit -> ledger/snapshot
#      accounting -> result — and prints CRITICAL APPLICATION PATH PASSED.
# 4. Whole-file deletes removed ~408 tests of clear LOW/REDUNDANT/OBSOLETE
#    value. Per-suite trims removed several hundred more micro-tests while
#    keeping the strongest test per behavior.
#
# RUNNING IT
# ==========
#   pytest $(cat tests/critical_suite.txt | tr '\n' ' ')
# or on Linux CI (bash): readarray -t F < tests/critical_suite.txt; pytest "${F[@]}"
#
# WHAT WAS REMOVED (categories)
# =============================
# - trivial getters/setters / constructors / DTO assignments / enum conversions
# - private implementation details and exact internal call-order assertions
# - tests that only confirm a mock returned what it was configured to return
# - duplicate integration tests and duplicate serialization/roundtrip tests
# - log-formatting and telegram-HTML-formatting variant tests (core notifier kept)
# - CI-reporter-internal and git-operation bookkeeping tests
# - release build-script presence / ps1-parse cosmetic tests (pipeline covers)
# - latency/perf micro-measurement tests without threshold authority
# - MFE/MAE forensics and exit-behavior forensics duplicated by trade-lifecycle
# - obsolete single-bug forensic suites (BUG-046/BUG-081) whose contracts moved
#   into permanent suites
# - MT5 raw-fixture mapping verification (mapping tested via real-history suites)
#
# KEPT / STRENGTHENED (categories)
# ================================
# - ACCOUNTING: test_accounting_core(65), trade_lifecycle(26), metric_truth(32),
#   accounting_api + mt5 accounting contract — untouched, strongest suites kept.
# - RISK: risk_engine(8), execution_architecture, order_lifecycle,
#   order_manager_exit_bugs(11), hardened_protocol, policy — kept.
# - MODEL/TRAINING/CHAMPION: model_governance, shadow_phase11, shadow70_runtime,
#   model_lifecycle, 70d_model_validation, model_generation, benchmark — kept.
# - LEAKAGE/CONTRACT: schema_70d_reconciliation(28), 70d_contract_parity,
#   liquidity_engine_causality(17), 70d_replay_parity, news_bridge_contract —
#   kept (data leaks invalidate ALL research, so these are privileged).
# - NEW: tests/unit/test_critical_suite.py (whole-cycle heartbeat + risk 1% guards).
#
# WHAT STILL LIVES IN heavy-ci
# ============================
# integration, e2e (playwright), research-backtest, model-validation matrix runs
# stay gated behind ci-tests branch / manual dispatch. They were NOT deleted;
# they just no longer gate normal pushes.
#
# MEASUREMENTS (before -> after)
# ==============================
# - Collected tests:     2335 -> ~2051 (full run incl. parallel additions)
# - Critical suite:      792 tests / 38 files
# - Deleted files:       40 (Phase A) + trims in 16 suites (Phase B)
# - Baseline full runtime:~22 min (1302 s) -> measured after run (see report)
# - Critical runtime:    measured (see report)
#
# HONEST GAPS
# ===========
# - The 4 pre-existing environment-failing tests (BUG-118 cold-start captures,
#   web_security log capture, mt5 accounting fixture, release hardening local
#   fixture) fail on THIS host but pass on CI where artifacts exist; they are
#   part of suites kept for their artifact-bearing environments.
# - 70D artifact-dependent tests SKIP when artifacts are absent (honest skips).
# - Parallel swarm agents add new suites (strategy_factory_phase22, runtime
#   config, research observability) — they joined the critical set when they
#   protect factory lifecycle / config hot-reload; other new suites remain in
#   the extended set by default.
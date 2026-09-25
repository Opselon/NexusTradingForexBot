# Agent Handoff — Test Suite Safe Consolidation (2026-08-19)

Agent: Hermes-TestConsolidation
Role: Test suite audit & safe consolidation
Branch: main
Commit: d4e2614
Date: 2026-08-19

## Summary

Performed a careful audit of the entire test suite (2,304 collected tests,
129 files) and consolidated redundancy while preserving all critical coverage.

## Method

1. **Inventory** — pytest collection (2,304 tests across tests/unit|integration|release;
   playwright e2e excluded as expected). Baseline run: 2,292 passed, 7 pre-existing
   failures, 5 skipped, ~19.5 min serial.

2. **Deep per-test analysis** — 11 parallel subagents read every test file,
   cross-referenced against src/ modules, git history, and bug ledger
   (agents/bugs.md), and assigned tiers:
   - Tier 1 (critical: risk, execution, accounting, model validation, regressions): 1,055
   - Tier 2 (important: services, adapters, API, integrations): 856
   - Tier 3 (redundant/duplicate): 142
   - Tier 4 (obsolete/dead): 1

3. **Duplicate detection** — AST-normalized body hashing found only 3 near-duplicate
   groups suite-wide (suite was already remarkably clean).

4. **Quarantine (nothing deleted permanently)** — moved to `_cleanup_hold_20260819/`
   preserving directory structure + README manifest:
   - 51 redundant tests where a stronger sibling remains in the active suite
   - 3 permanently-skipped (skipif(True)) perf probes from test_70d_perf_task3
   - 1 obsolete stub (test_liq45_manifest_records_60d)

5. **Consolidation** — htf_warmup_gate test_1/test_2 (insufficient H1/H4 history)
   merged into one parametrized test_insufficient_history_results_in_not_ready.

## Results

- Collection: 2,304 → 2,276 tests (-28)
- Modified files: 30 (28 quarantine-source + htf consolidation + perf cleanup)
- Modified-file run: 701 passed, 1 pre-existing failure
  (test_period_report_has_real_financials — baseline, test DB has no MT5 history),
  201 skipped
- Ruff clean on all modified files
- Source code untouched (git diff src/ shows only parallel-agent WIP)

## Preserved (Tier 1) — NOT touched
- Risk engine, lot sizing, stop-loss, equity scaling
- Order execution, position lifecycle, exit behavior, pending-cancel reconciliation
- PnL/accounting, peak tracking, period aggregation
- Model training/validation, walk-forward/OOS floors, governance gates
- Feature contracts (50D/60D/70D), causality/leakage prevention, schema hashes
- Database persistence, migrations, hygiene worker
- All BUG-xxx regression guards (BUG-046/054/072/074/080/081/082/086/091/099/106/110/118)
- Deploy gate, incident response, latency budgets, monitoring families

## Files Changed
tests/ (30 files) + _cleanup_hold_20260819/ (32 files: 28 unit + 1 integration +
README + 2 audit docs)

## Rollback
Restore any quarantined test by copying the original from
`_cleanup_hold_20260819/<unit|integration>/<file>.py` back to `tests/<unit|integration>/`.
The README manifest lists every moved test with its reason.

## Known Risks
- The 7 baseline failures are pre-existing environment/data-dependent (no MT5
  history in test DB, stale news-source freshness window, Web/ DOM drift from
  parallel WIP) — not caused by this consolidation.
- Parallel agents were actively modifying some test files during the audit
  (conftest, test_research_api, test_debug_snapshot_phase20, test_incident_response_task12).
  None were touched; their WIP remains in the working tree.

## Exact Next-Agent Instructions
1. Do NOT re-run the quarantine (already committed in d4e2614).
2. If any quarantined test is later found to protect a real regression, restore it
   from _cleanup_hold_20260819 immediately (see README manifest).
3. The 7 pre-existing failures should be triaged separately:
   - test_post70d_monitoring_activation.test_http_200_with_articles_healthy (stale
     freshness window — data-dependent)
   - test_web_security.test_06_server_log_contains_detailed_exception (log capture)
   - test_hardened_protocol.test_model_rollback_on_health_check_failure
   - test_frontend_assets_phase14.test_all_getelementbyid_refs_exist (Web/ DOM drift
     from parallel WIP)
   - test_release_* failures (release artifact expectations)
   - test_mt5_accounting_api_contract.test_period_report_has_real_financials (no MT5
     history in test DB)
# Test Suite Cleanup Hold — 2026-08-19

Quarantined during the Safe Test Suite Consolidation pass. All tests here are **fully recoverable** 
originals (complete file copies under `unit/`, `integration/` mirroring `tests/` layout).

## Policy
- **Nothing was permanently deleted.** Move back any test by restoring the file from this directory.
- Tier 3 = redundant/duplicate (stronger sibling remains active in `tests/`).
- Tier 4 = obsolete (tests deleted/altered functionality).
- Tests protecting known BUG regressions were **kept** — none of them are here.

Total tests quarantined: **54** (51 in-file + 3 dead perf probes + 2 consolidated htf)

## Manifest

| File | Test | Tier | Reason |
|---|---|---|---|
| test_70d_inference_validator_task3.py | test_p10_scaler_never_padded_or_truncated | T3 | Exact duplicate of test_p09_scaler_dimension_mismatch_blocked (same validator+validate, weaker asserts — no reason/'60' check); no-pad intent documented in p09 comment |
| test_70d_inference_validator_task3.py | test_p13_70d_model_blocks_70d_runtime_with_60d_model | T3 | Exact duplicate of test_p13_60d_model_blocks_60d_runtime (identical compatible_model_schema call, weaker asserts — no reason check) |
| test_70d_parity_task3.py | test_03_04_70d_feature_ordering | T3 | Full duplicate of test_03_16's first half + test_schema_70d_02/test_current_70d_15 liquidity-name pins |
| test_70d_parity_task3.py | test_03_20_vector_hash_agreement | T3 | Redundant given test_03_01's 1e-12 equality (equal values => equal hash trivially) |
| test_70d_replay_parity_task3.py | test_p30_liquidity_slices_distinct_from_base | T3 | Tautological: first assert is `v[60:70] != [0.0]*10 or True` (always true); news-block-zero check repeats p30_news_off |
| test_accounting_advanced_metrics.py | TestAdvancedMetrics::test_blanket_no_crash_on_single | T3 | Redundant smoke; subsumed by test_empty_inputs and test_basic_stats |
| test_accounting_core.py | TestProvenanceSurvival::test_feature_schema_dimension_forward_compat | T3 | Schema-dimension pin belongs in schema-contract suite (test_schema_70d_reconciliation covers it) |
| test_accounting_core.py | TestWorker::test_worker_idle_when_stopped | T3 | Trivial tick()-returns-False; covered by start/stop and throttle tests |
| test_anomaly_verify01_mfe.py | test_anom23_mfe_anomaly_severity_is_low | T3 | Trivial severity-constant assertion |
| test_intelligence_phase09.py | TestFeatureSchemaMigration::test_old_schema_records_stay_valid | T3 | Subset of exp_intel test_33 (same v1/v2 coexistence proof) |
| test_intelligence_phase09.py | TestGate::test_insufficient_evidence_passes_through | T3 | Duplicate of exp_intel test_19 (pass-through) |
| test_intelligence_phase09.py | TestSelfHealing::test_rebuild_derived_from_immutable_ledger | T3 | Subset of exp_intel test_27/29/30 (same rebuild-from-ledger proof) |
| test_liquidity_engine_contract.py | test_liq37_same_input_same_output_swings | T3 | Redundant with liq36 + optimization determinism tests |
| test_liquidity_engine_features.py | test_config_liquidity_switch_defaults_false | T3 | Duplicate of task02_01 (same config-default assert) |
| test_liquidity_engine_features.py | test_liq45_manifest_records_60d | T4 | Obsolete stub: hard-coded 50D id overwritten with constants; asserts nothing real |
| test_liquidity_optimization_phase19.py | test_liq_opt_15_confluence_dedup_not_inflated | T3 | Dup of features liq17 semantics on v1.1 copy |
| test_liquidity_optimization_phase19.py | test_liq_opt_18_producer_single_source | T3 | Structural no-IO check dup of liq34/35 |
| test_liquidity_optimization_phase19.py | test_liq_opt_21_results_file_exists | T3 | Soft evidence probe of scratch file; near-redundant with opt08 |
| test_liquidity_task02_integration.py | test_task02_21_liquidity_schema_registered_for_60d | T3 | Duplicate of liq01 registry check |
| test_model_benchmark_phase13b.py | test_30_phase_regression_imports | T3 | Import-only regression; trivial |
| test_model_generation_phase13.py | test_44_news_cannot_bypass_policy | T3 | Tautological source-scan assertion |
| test_model_generation_phase13.py | test_48_phase08_imports_intact | T3 | Import smoke; covered by live imports everywhere |
| test_model_generation_phase13.py | test_49_phase09_imports_intact | T3 | Import smoke; covered by live imports everywhere |
| test_model_generation_phase13.py | test_50_phase10_imports_intact | T3 | Import smoke; covered by live imports everywhere |
| test_model_generation_phase13.py | test_51_phase11_imports_intact | T3 | Import smoke; covered by live imports everywhere |
| test_mt5_accounting_api_contract.py | test_mt5_status_history_endpoint_still_available | T3 | Weak smoke: asserts only 200-or-500 range, no behavior |
| test_mt5_database_persistence.py | test_tables_exist_after_repo_create | T3 | Schema presence only; every other test in file fails loudly if tables missing |
| test_mt5_providers_phase14.py | test_deal_net_result_subtracts_costs | T3 | Duplicate of raw_fixtures test_deal_net_result_sign_convention (same profit-costs formula); raw_fixtures one retained |
| test_mt5_providers_phase14.py | test_none_raw_returns_unavailable | T3 | Duplicate of raw_fixtures test_account_null_fields_stay_none (same None->UNAVAILABLE semantic) |
| test_mt5_providers_phase14.py | test_sql_timestamp_string | T3 | Near-identical to test_iso_string_naive_treated_as_utc (same input, parse path, hour/min asserts) |
| test_mt5_raw_fixtures.py | test_account_null_fields_stay_none | T3 | Duplicate of phase14 test_none_raw_returns_unavailable (same semantic); phase14 one retained |
| test_mt5_status_endpoint.py | test_forced_disconnect_state | T3 | Direct fake-adapter assert; endpoint tests already prove via JSON |
| test_order_manager_audit.py | test_no_legacy_import_references_in_repo | T3 | One-time forensic sweep after module removal; near-tautology |
| test_order_manager_audit.py | test_order_manager_audit_telemetry | T3 | Asserts compile-time constants (DEAD/REMOVED/legacy path); one-time migration forensics |
| test_order_manager_audit.py | test_project_file_cleanup | T3 | Same intent as test_no_legacy_import_references_in_repo (one-time cleanup) |
| test_outcome_correlation_phase14.py | TestFailureIsolation::test_experience_failure_does_not_raise | T3 | Subset of exp_intel test_38 (same record_trade_outcome failure-proof) |
| test_outcome_correlation_phase14.py | TestSlTimeline::test_initial_vs_final_sl_distinct | T3 | Trivial string-constant assertion |
| test_performance_metric_truth.py | TestBrokerReconciliation::test_unknown_pnl_not_fabricated | T3 | Placeholder (assert True); real guard is normalizer tests |
| test_performance_report_intelligence.py | TestStrategyAttribution::test_strategy_attribution | T3 | Weak assert (accepts empty OR >=1); real attribution covered in accounting tests |
| test_release_manifest_phase19.py | test_manifest_model_compatibility_all_schemas | T3 | String-content assert; redundant with supported-schemas test |
| test_release_system.py | test_release_artifact_names_identify_architecture | T3 | String-format assert, trivially true |
| test_release_update_phase17.py | test_github_dns_failure_returns_error | T3 | Same status_for_exception path as 429 test; redundant |
| test_release_update_phase17.py | test_helper_bootstrap_detection | T3 | Only checks a helper file exists; redundant with install-mode test |
| test_release_update_phase17.py | test_onefile_cli_detection | T3 | Only asserts a .exe filename can be created; doesn't test detection logic |
| test_shadow_phase11.py | test_accounting_intact | T3 | Import smoke; dup across suites (model_lifecycle_phase10 retained) |
| test_shadow_phase11.py | test_phase08_experience_intact | T3 | Import smoke; dup across suites (model_lifecycle_phase10 retained) |
| test_shadow_phase11.py | test_phase09_research_intact | T3 | Import smoke; dup across suites (research_phase09b retained) |
| test_shadow_phase11.py | test_phase10_model_lifecycle_intact | T3 | Import smoke; dup across suites (model_lifecycle_phase10 retained) |
| test_telegram_forensics_bug072.py | test_success_returned | T3 | Duplicate of test_telegram_notifier_send_success (same ok:true send path); notifier one retained |
| test_trade_lifecycle_task3.py | test_tl18_liquidity_reversal_is_captured | T3 | Appends event dict directly to internal map — no production classifier exercised |
| test_trade_lifecycle_task3.py | test_tl20_feature_schema_metadata_survives_lineage | T3 | Off-topic feature-schema validation; covered by schema-contract suites |
| tests/unit/test_70d_perf_task3.py | test_70d_construction_and_validation_bounded + test_validator_cached_metadata_fast + test_runtime_hook_snapshot_bounded | T3 | Permanently skipped (skipif(True)) opt-in timing probes; dead test-suite members (INV-001 no-DB test retained) |
| tests/unit/test_htf_warmup_gate.py | test_1_insufficient_h1_history_results_in_not_ready + test_2_insufficient_h4_history_results_in_not_ready | T3 | Consolidated into one parametrized test_insufficient_history_results_in_not_ready (2 cases) — same behavior, clearer intent |

Total entries: 53
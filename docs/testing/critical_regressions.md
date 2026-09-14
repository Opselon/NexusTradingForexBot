# Critical Regression Registry — NSE

> Project testing memory (brief section 30). **Future agents MUST consult this
> file before deleting, demoting, or rewriting any test listed here.**
> Each entry: what broke in reality, how it is reproduced, which test now owns
> the protection, and severity. Sources: `agents/bugs.md`, taskboard landed
> rows, and the 2026-09-14 forensic reconstruction lanes.
>
> Severity key: **P0** = can move money / corrupt economic state / trade when
> it must not. **P1** = wrong evidence, silent dishonesty, boot/roll blocking.
>
> Tier key: T0 static · T1 PR-critical (`tests/critical_suite.txt`) ·
> T2 extended (`tests/extended_suite.txt`, main push) · T3 cross-platform
> (`tests/windows_skew_suite.txt` + full suite on Windows/main) ·
> T4 release/forensic (nightly-qa, heavy-ci, release).

| ID | Failure (real, reproduced) | Subsystem | Reproduction condition | Protecting test(s) | Sev | Tier |
|----|---------------------------|-----------|------------------------|--------------------|-----|------|
| CR-001 | Duplicate dispatch of the same `request_id` reached the broker a second time | execution | fire dispatch_order twice with identical request_id | `test_agent12_execution_forensic.py::test_duplicate_request_id_never_reaches_broker`, `test_capital_protection_a15.py` N3 | P0 | T1 |
| CR-002 | Engine-wide exposure gate counted only one symbol; second position opened cross-symbol | risk/execution | open XAUUSD then attempt EURUSD | `test_agent11_execution_risk_forensic.py` (BUG-240), `test_agent12_execution_forensic.py::test_max_total_exposure_blocks_second_dispatch` | P0 | T1 |
| CR-003 | Ambiguous broker write treated as FAILED → blind retry → duplicate live order | execution/write | transport timeout, no response | `test_order_write_uncertainty.py` (tri-state UNKNOWN≠FAILED, intent survives restart) | P0 | T1 |
| CR-004 | Redelivered signal/execution created a SECOND durable economic row after process restart | DB economic integrity | fresh AuditRepository over same file, same dedup identity | `test_recon_duplicate_deal_across_restart.py`, `test_bug254_executions_idempotency_migration.py` | P0 | T1 |
| CR-005 | One bad row in a worker batch discarded up to 499 good financial rows | audit DB | enqueue good rows + one native IntegrityError, flush | `test_recon_batch_atomicity_real_failure.py`, `test_audit_flush_contract.py` | P0 | T1 |
| CR-006 | Flushed rows lost on ungraceful process death (WAL/durability claim never proven) | audit DB | child process `os._exit` after flush, reopen | `test_recon_wal_crash_durability.py` | P0 | T1 |
| CR-007 | ON CONFLICT targets dead-lettered FOREVER on gate-first databases (15 of them) | DB bootstrap | run migration gate before app bootstrap, insert producer upserts | `test_bug276_unique_constraint_shadow.py` (T2), `test_perf_deadletter_skeleton_repro.py` (T2) | P0 | T2 |
| CR-008 | Reversal (AI flip) path bypassed `evaluate_proposal` entirely | risk | flip enabled + risk reject | `test_bug258_reversal_risk_bypass.py` | P0 | T1 |
| CR-009 | Micro-account margin resurrection: zeroed volume resurrected to broker-min unaffordable (BUG-239 UnboundLocalError class) | risk | equity<50, margin insufficient | `test_agent11_execution_risk_forensic.py`, `test_recon_risk_boundary_battery.py` (margin gate mutation-KILLED) | P0 | T1 |
| CR-010 | Breaker budget laundered across restart (same-day breach cleared) | risk | persist anchors, restart, re-evaluate | `test_bug259_breaker_anchor_restart.py`, `test_risk_circuit_breakers.py` | P0 | T1 |
| CR-011 | Circuit breaker dead on the primary dispatch path (SAFE_MODE never fed) | execution | 3 consecutive broker refusals | `test_agent11_execution_risk_forensic.py` (BUG-241), `test_agent12_execution_forensic.py::TestAgent12SafeMode` | P0 | T1 |
| CR-012 | Web `/api/positions/close` called the adapter directly (only risk-bypass surface) | web/execution | close via API vs manager audit rows | `test_agent11_execution_risk_forensic.py` (BUG-242) | P0 | T1 |
| CR-013 | PAPER engine ran with LIVE adapter / persisted LIVE beat explicit PAPER override at boot | mode isolation | mode_override vs settings DB precedence | `test_packaged_db_and_mode_bug146_149.py`, `test_bug232_mode_boundary.py`, `test_system_integration_boundaries.py` | P0 | T1 |
| CR-014 | Replay toggle flipped a PAPER engine to LIVE (historic defect class) | mode isolation | REPLAY off restores PRIOR mode, never LIVE | `test_replay_toggle_guard.py` | P0 | T1 |
| CR-015 | Shadow observations executed against the broker | shadow isolation | SHADOW mode decision → zero order_send | `test_shadow70_safety.py`, `test_shadow_phase11.py`; mutation anchor MUT-SHADOW-BOUNDARY | P0 | T1 |
| CR-016 | Paper data trained a production-eligible model (lineage laundering) | model provenance | PAPER/UNKNOWN lineage → promotion ineligible | `test_paper_live_training_lineage.py`, `test_bug226_paper_provenance.py` | P0 | T1 |
| CR-017 | 50D artifact served under a 70D bundle contract (scaler/dim silent pass) | model contract | dim mismatch must refuse, no pad/truncate | `test_bug141` (declared dim), `test_70d_inference_validator_task3.py`, `test_schema_70d_reconciliation.py`, `test_a2_data_lineage_bounded.py` | P0 | T1 |
| CR-018 | Retrained champion not reflected in registry → boot trust-anchor refused the next cold boot permanently (BUG-271) | model lifecycle | in-place governed replace + restart | `test_bug271_champion_retrain_fingerprint.py`, `test_bug257_champion_drift_sentinel.py` | P0 | T1 |
| CR-019 | Fresh-init (untrained) weights promoted as champion | model health | byte-equality canary + behavioral gate | `test_bug225_untrained_champion_canary.py`, `test_promotion_rejects_degenerate_model.py` | P0 | T1 |
| CR-020 | Backtest with ZERO friction validated fantasy edge into promotion | economics | zero-cost config rejected unless explicit | `test_zero_friction_guard_e1.py`, `test_promotion_economic_integrity.py`, `test_research_purge_defaults_bug183.py` | P0 | T1 |
| CR-021 | Walk-forward leakage: purge/embargo defaults of 0 shipped (BUG-183 class) | training causality | production gate defaults = leakage constants | `test_research_purge_defaults_bug183.py`, `test_dataset_split_purge_bug244.py`, `test_agent16_walkforward_purge_embargo_leakage.py`; anchors MUT-TEMPORAL-PURGE/EMBARGO | P0 | T1 |
| CR-022 | Future bars altered historical feature vectors (INV-008) | features | replay parity + gap-safety + vector causality | `test_gap_safe_sequences.py`, `test_70d_replay_parity_task3.py`, `test_qa_deep_metamorphic_replay.py` | P0 | T1 |
| CR-023 | Stale market feed presented as READY (silent-stall incident class) | freshness/health | frozen feed → BLOCKED_BY_STALE, health STALE | `test_live_freshness_g29.py` (T2), `test_live_freshness_characterization.py` (T1); anchor MUT-FRESHNESS-GATE | P0 | T1/T2 |
| CR-024 | Duplicate/out-of-order ticks corrupted the M1 bar series and re-fired decisions | market data | duplicate tick idempotency, future-stamp refusal | `test_market_data_integrity_ag13.py` | P0 | T1 |
| CR-025 | Gate rejected a proposal yet a dispatch was still attempted (experience layer) | execution boundary | harmful strategy → zero dispatch attempts | `test_experience_execution_boundary.py` (integration, T2), `test_gate_convergence_evidence.py` (T1) | P0 | T1/T2 |
| CR-026 | Drawdown halt computed from stale config instead of runtime snapshot (BUG-132) | risk | max_account_drawdown_pct hot value | `test_survival_drawdown_runtime_bug132.py`, `test_agent5_bug249_251_252.py` (BUG-252 peak forwarding) | P0 | T1 |
| CR-027 | Maintenance window: entries dispatched during broker rollover; missing timestamp treated as safe | execution guard | tick inside 23:00–01:00 server window | `test_maintenance_dispatch_guard.py`, `test_bug264_maintenance_pin_collision.py` | P0 | T1 |
| CR-028 | Missing CI results JSON reported "ALL CHECKS PASSED" (silent-green hole) | CI integrity | delete a run-info file, run final gate | `test_ci_final_gate_fail_closed.py`, `test_gate_scope_integrity.py` | P1 | T1 |
| CR-029 | Green deploy-gate despite one check never running at merge | release gate | release auth matrix incl. pending/skipped | `test_release_auth_gate.py` (one env-dependent test reports live-branch drift — accepted, documented) | P1 | T1 |
| CR-030 | Update installed a tampered/downgrade payload; rollback lost user DB | release/update | signed-manifest refusal, semver gates, data preservation | `test_release_update_phase17.py`, `test_signed_update_manifest.py`, `test_bug263_snapshot_integrity_gate.py` | P0 | T1 |
| CR-031 | Split fills double-counted as separate economic trades (BUG-081) | accounting | one parent order, N fills → one canonical trade | `test_trade_lifecycle_task3.py` tl02/tl03, `test_accounting_deduplication.py` | P0 | T1 |
| CR-032 | Broker PnL figures fabricated as defaults/zero with history present | accounting | fixture ground-truth equality | `test_mt5_accounting_api_contract.py` (T1), `test_performance_metric_truth.py` | P0 | T1 |
| CR-033 | Pending churn-lock: re-quote storm without the 30s+1×ATR AND-gate; stale cache stuck exposure gate open | execution/pending | both legs isolated (this reconstruction's gap) | `test_recon_requote_and_reconcile.py` (mutation-KILLED) | P0 | T1 |
| CR-034 | News out-of-range state poisoned the 70D vector / silently clamped LIMIT truncated training frames | news/70D | [-3,3] bounds; truncation warnings | `test_bug217_news_state_bounds.py` (T1), `test_news_db_truncation_honesty.py` (T2) | P1 | T1/T2 |
| CR-035 | `persist=False` ignored: quality-rejected candidate overwrote the artifact (BUG-228) | model gen | early-exit paths never re-persist | `test_rejected_candidate_not_persisted.py`, `test_p0_producer_regression.py` | P0 | T1 |
| CR-036 | Test harness wrote the production champion artifact/registry (BUG-276 wave) | harness safety | sandboxed artifact root assertions | `test_bug276_test_harness_artifact_isolation.py` (wave branch), `test_lifecycle_chaos.py` | P1 | T2 |

## Standing rules

1. A file named above is **never** deletable without moving its row to a new
   owner first (the registry travels with the tests — brief section 4).
2. Adding a Tier-1 entry requires: measured cost, the defect it catches in one
   sentence, and (for P0 safety families) a KILLED mutation in
   `scripts/testing/mutation_check.py` or `scripts/qa/run_mutations.py`.
3. The nightly mutation campaign (`nightly-qa.yml:mutation-campaign`) is the
   canonical executor for the anchor catalog; `scripts/testing/mutation_check.py`
   covers the reconstruction-added batteries. Survivors = blind spots = bugs.
4. `tests/unit/test_bug276_unique_constraint_shadow.py` is the model citizen
   for DB-contract batteries: if a NEW `ON CONFLICT` producer is added to src/,
   `APP_UNIQUE_TARGETS` + this test must cover it (repo-wide scan at :385).

# NSE Test Value Matrix

Generated 2026-09-15T17:35:47+00:00 from measured evidence. Machine-readable source: `docs/testing/test_inventory.json`.

## Scoring model (explainable, not vanity)

```
TestValue      = ProductionRelevance x DefectDetectionPower x Determinism x RuntimeEfficiency
CostEfficiency = TestValue / measured-worker-seconds
```

ProductionRelevance = f(count of production modules the file imports/exercises — AST-verified).
DefectDetectionPower = forensic judgment (PROMOTE-GATE .95 / KEEP .8 / MERGE,REWRITE .6 /
CONVERT .4 / DELETE .25), capped to .4 when mock-heavy and unreviewed. UNREVIEWED = .5 neutral.
Determinism = judgment (deterministic 1.0 .. random-uncontrolled .3); xfails cap to .4.
RuntimeEfficiency = measured aggregate worker-seconds (<=2s:1.0, <=10s:.9, <=60s:.7, <=200s:.5, <=600s:.2, >600s:.1).

## Classification tally (481 test files)

- **KEEP**: 205
- **UNREVIEWED**: 153
- **MERGE**: 39
- **KEEP-PROMOTE**: 26
- **DEMOTE**: 22
- **PROMOTE-GATE**: 14
- **REWRITE**: 8
- **CONVERT-RUNTIME**: 7
- **PROMOTE-GATE-APPLIED**: 7
- **DELETED**: 5
- **DELETE-CHECK**: 3
- **DELETE**: 1

## Tier-1 cost outliers REMOVED from the PR gate (moved to Tier 2, evidence-driven)

| file | measured worker-s | what it protects | where it runs now |
|---|---|---|---|
| `tests/unit/test_bug276_unique_constraint_shadow.py` | 1252 | strong production-truth test; cost is the parametrized ON CONFLICT battery — belongs on main-push lane, not PR lane | tests/extended_suite.txt (main-push) |
| `tests/unit/test_perf_deadletter_skeleton_repro.py` | 730 | same DB-boot family | tests/extended_suite.txt (main-push) |
| `tests/unit/test_news_db_truncation_honesty.py` | 428 | module-scoped real-DB fixture cost; content authoritative | tests/extended_suite.txt (main-push) |

## Promoted INTO the PR-critical gate (P0 safety domains)

29 files classified PROMOTE-GATE by the forensic lanes and added to
`tests/critical_suite.txt` (aggregate measured cost ~20 worker-seconds). Domains:
risk math/circuit breakers, execution write-uncertainty & idempotency, DB economic
integrity, PAPER/LIVE mode isolation, model artifact identity/lineage, market-data
tick integrity. Per-file defect statements: `docs/testing/critical_regressions.md`.

## New reconstruction-owned Tier-1 batteries (TDD-validated: 7/7 mutations KILLED)

| file | invariant | mutation evidence |
|---|---|---|
| `test_recon_risk_boundary_battery.py` | margin clamp fraction (default 10% / hard 20%), tier-boundary exclusivity, floor-to-step float eps, hostile risk_pct/free-margin | MUT-RECON-MARGIN/TIER/FLOOR/MARGIN-GATE: KILLED |
| `test_recon_wal_crash_durability.py` | WAL boot, crash durability of flushed financial rows, in-memory no-CWD-leak | MUT-RECON-WAL: KILLED |
| `test_recon_batch_atomicity_real_failure.py` | one natively-failing row never destroys the batch; dead-letter replayable | manual: salvage asserts fail when recovery loop skipped |
| `test_recon_duplicate_deal_across_restart.py` | signal/executions UNIQUE identity survives process restart (INV-006 cross-boot) | DB-level unique identity pinned by redelivery collapse |
| `test_recon_requote_and_reconcile.py` | 30s re-quote lock release legs (time AND drift), pending-cache broker-truth repair | MUT-RECON-REQUOTE/RECONCILE: KILLED |

## Deleted (evidence per file — brief section 4 answered for each)

| file | why safe to remove |
|---|---|
| `tests/unit/test_p2a_report_queries_golden.py` | refactor-completed golden; writes cross-run file under LOCALAPPDATA; skips=2 in CI (proven by baseline run) |
| `tests/unit/news/test_pro_auto.py` | mock-return asserts; exact overlap with test_news_pro_auto_console.py; 3/3 baseline run showed failing on mock shape |
| `tests/unit/test_no_future_leakage.py` | all 3 tests skip in CI (needs gitignored local parquet) = silent no-op; causality already pinned by test_gap_safe_sequences + test_70d_replay_parity_task3 (critical) + qa_deep_metamorphic_replay |
| `tests/integration/test_engine_runtime_launch.py` | not a launch test (log+source grep), skips in CI, writes repo artifacts on failure; startup certification owned by scripts/ci/runtime_gate.py + test_runtime_gate_e2e |
| `tests/unit/test_hist_sim_golden.py` | COLLECTION ERROR at origin/main: imports src/nexus_scalp/research/historical/bars.py which never existed on main (git log --all: no history). Test targets deleted functionality; breaks every full-suite run. |
| `tests/integration/test_playwright_e2e.py` | fixed port 9091 + sleep-paced selectors; superseded by tests/e2e_client journeys wired to nightly-e2e. Moved to tests/manual/ (kept, never deleted without replacement proof) |
| `tests/unit/test_forensic_monitoring_task11.py` | WAVE-2: 87 tests were merged into test_forensics.py (108 = 87+21, def-count superset verified via grep); file unmanifested anywhere; zero CR/bugs.md protection rows. The T1 anchor runs the union. |
| `tests/unit/test_forensic_incident_center_task.py` | WAVE-2: 21 tests merged into test_forensics.py; same arithmetic; unmanifested; no CR row |
| `tests/unit/test_incident_response_task12.py` | WAVE-2: 66 tests merged into test_incidents.py (92 = 66+26 verified); unmanifested; no CR row |
| `tests/unit/test_incident_runtime_task13.py` | WAVE-2: 26 tests merged into test_incidents.py; unmanifested; no CR row |
| `tests/unit/test_dead_letter_store_split.py` | WAVE-2: A4-refactor delegation mirror ('store owns table, facade delegates' = §12 implementation-detail); durable dead-letter behavior owned by dead_letter_retention_cap (T2 now) + native-failure salvage battery test_recon_batch_atomicity_real_failure; unmanifested, no CR row |

## Moved to explicit manual/nightly lanes (never deleted without replacement proof)

- `tests/integration/test_playwright_e2e.py` -> `tests/manual/` (superseded by e2e_client journeys on nightly-e2e)

## Converted to runtime/lint checks

- `tests/unit/test_decision_id_integrity.py` — git-tree scan; moved to scripts/ci (check_decision_ids already runs in ci-integrity job)
- `tests/unit/test_node_runtime_role.py` — repo-layout absence probes (no node runtime) -> lint job, not unit CI
- `tests/unit/test_wsl2_platform_registry.py` — CRLF/eol hygiene -> pre-commit/CI lint; keep the docs-claim pin in docs lane
- `tests/unit/test_agent16_hotpath_perf.py` — timing assertions flake under CI load; move to scheduled benchmark
- `tests/cli/test_docs_consistency.py` — zero test_ functions -> silently collected nothing; main() moved to scripts/ci/check_docs_cli.py invoked by docs job
- `tests/unit/test_gate_bypass_detection.py` — WAVE-2 fixture census: 5213 worker-seconds (11 full git-archive HEAD materializations + a check_local subprocess per test) — real behavior (gate-bypass detectability) so NOT deleted, but it belongs to the ci-gate-tooling lane, never a PR tier. Blessed-tree materialize-once optimization (~350s saving) is a follow-up rewrite.

## Rewrites required (kept in suite, defect noted; next wave)

- `tests/unit/test_packaged_db_and_mode_bug146_149.py` — PROMOTION REVERTED (measured 2026-09-15): two engine-boot tests build a REAL LiveEngine against the machine's artifacts/models/.../model.pt; a pre-trust-chain LEGACY_UNVERIFIED artifact on the dev box makes the boot refuse (ArtifactIntegrityError) — environment-dependent, non-CI-safe. The mode-isolation invariant itself is carried by test_bug232_mode_boundary.py + test_replay_toggle_guard.py (promoted). -> rewrite onto a provisioned starter bundle (bug269 provisioner pattern) before re-promoting (tier T2)
- `tests/integration/test_database_execution_audit.py` — writes fixed artifacts/test_audit.db — xdist-unsafe + pollutes the repo tree -> tmp_path (production paths still exercised) (tier T1)
- `tests/integration/test_signal_pipeline_health.py` — same fixed-path DB pollution; near-duplicate of the file above -> tmp_path + merge the two into one pipeline-audit file (tier T1)
- `tests/integration/test_model_lifecycle_api.py` — test_engine_mode_apply_and_persist writes the machine REAL settings DB (load_settings_service without NEXUS_SETTINGS_DB isolation) + 25 LiveEngine boots -> isolate + shared engine fixture (tier T2)
- `tests/unit/test_perf02_account_refresh_throttle.py` — zero production imports — tests a hand-copied mirror of live_engine code -> rewrite against the real refresh gate (tier T1)
- `tests/unit/test_rule_matrix.py` — mocks policy/order-manager hooks — the internals it claims to verify -> rewrite with real SignalPolicy on rule chain (tier T2)
- `tests/integration/test_news_api.py` — one test performs a real network fetch attempt (ingest_cycle) — nondeterministic/offline-slow -> inject fetcher boundary (tier T2)
- `tests/integration/test_runtime_70d_contract_probe.py` — one test asserts against machine-configured artifact with NO skipif -> red on clean clones -> honest skip when artifact absent (tier T2)

## Deferred merge groups (recorded, NOT executed blind)

- **accounting-aggregation**: anchor `test_accounting_pnl_regression.py + test_accounting_deduplication.py (both critical)`; members test_accounting_core.py(87); test_accounting_hedging.py; test_accounting_normalize.py — DEFERRED-MERGE: content valuable, consolidation requires per-case triage beyond this branch's budget — recorded for next wave
- **news_bridge**: anchor `test_news_bridge_contract_phase13b.py + _finalize (both critical)`; members test_news_bridge_phase13b.py(11) — DEFERRED-MERGE
- **shadow70**: anchor `test_shadow70_runtime.py + test_shadow70_safety.py`; members test_shadow_phase11.py; test_shadow_replay_evidence_chg0047.py; test_shadow70_news_family.py — DEFERRED-MERGE
- **obs_contract**: anchor `test_observability_guardrails.py + test_observability_contract_freeze.py (critical)`; members test_operational_log_hygiene.py; test_qa_deep_observability_evidence.py — DEFERRED-MERGE
- **golden_characterization**: anchor `test_qa_deep_state_machines.py (critical)`; members test_s2_state_machine_golden.py; test_s3_recovery_budget_golden.py — DEFERRED-MERGE (characterization parity already achieved post-extraction)
- **incidents**: anchor `test_incidents.py(92, critical)`; members test_incident_runtime_task13.py; test_incident_response_task12.py(66) — DEFERRED-MERGE

## UNREVIEWED remainder

153 files carry machine facts only (no forensic verdict survived the
salvage). Policy: an UNREVIEWED file may NOT be deleted; it is tier-assigned by machine
signals (subprocess/sleep/network -> Tier2+, else inherits directory default) and stays in
the appropriate manifest. Completion of their judgment is the next wave's first task.

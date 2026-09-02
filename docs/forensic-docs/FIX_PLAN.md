# Forensic Fix Pass — Fix Plan (2026-08-20)

Agent: Hermes-Forensic-01. Repo: NexusTradingForexBot @ main.

## Findings summary (from docs/forensic-docs/04_ISSUES_LEDGER.md + git forensics)

| # | Issue | Root cause | Fix decision |
|---|-------|-----------|--------------|
| 1 | scalp_features.py:879 dead expression | `last_sl_val + 0.50 * (last_sh_val - last_sl_val)` computed, discarded | Remove (pure side-effect-free dead code; the equilibrium midpoint is already covered by feat_ob_equilibrium_ratio via ob_price). Add no behavior change. |
| 2 | Risk contract mismatch 0.50/2.00 vs 0.10/1.00 | skill.md §7 documents OLD tier table; code (risk_engine.py get_clamped_position_size:74-90 + calculate_dynamic_volume:191-201) is 0.02/0.10/1.00/min(10, vmax). CODE is truth (matches RiskConfig defaults 0.5% risk, HARD_MAX_LOTS 10.0, skill.md's own 0.5% default elsewhere). | Restore skill.md §7 table to code values; ALIGN everything: RiskEngine ctor default max_allowed_lots 50.0 → 10.0 (split-brain vs OrderManager HARD_MAX_LOTS=10.0); high_confidence_threshold ctor 0.70 align w/ config default 0.95 (ledger #11); add risk regression tests. |
| 3 | walk_forward_trainer.py:328-335 stale "TASK 1" log | Log block says 0=BUY,1=SELL,2=NO_TRADE; real label_map (line 207-213) is 0=NO_TRADE,1=BUY,2=SELL; "TASK 1" header is stale (no TASK-1 exists in train code). | Fix label lines (verify -> actual mapping), rename header to property-based label_map dump, remove hardcoded 3 class lines. |
| 4 | audit_repository.py SWARM damage | c617c0f (TASK-21 lint/format) introduced `self._driver` (undefined attr, 6 sites) + binding-count regressions (SQL keeps DATETIME('now'), args add datetime.now(UTC) -> count mismatch; get_account_performance_metrics broken too). Root: stash-merge aa55115 half-applied a driver refactor that 4c9b148 had completed for other files; c617c0f then "formatted" the broken state. NOT intentional, NOT valid — breaks every order/execution/snapshot write + reader path (AttributeError on live engine). | Restore pre-swarm behavior: per-method sqlite3.connect using self._db_path/_shared_conn; keep 4c9b148's ISO timestamps ONLY where the SQL was changed to `?` (audit_account_snapshots); restore DATETIME('now') for audit_orders/audit_executions (matching arg counts); rewrite get_performance_stats to pristine; keep c617c0f's other genuine fixes (UP031 etc.). |

## Risk pipeline trace (source of truth)

- Config: RiskConfig.risk_per_trade_pct default 0.5 (config.py:45); live.yaml runtime default 0.75; runtime_config.py defaults 0.5.
- RiskManager path: evaluate_proposal (risk_engine.py:424) risk_pct = config → regime ×0.5 / drawdown penalty ×0.2..1.0 / confidence ×0.5..1.2 → calculate_dynamic_volume (tier caps 0.02/0.10/1.00/min(10,vmax), 20% free-margin clamp, impact guard).
- Kelly: none in code — the "Kelly" in the brief maps to the confidence-scaled sizing block (Part 7).
- OrderManager `_clamp_volume` HARD_MAX_LOTS=10.0.
- Decision: CODE is truth. Docs (skill.md §7 tier table 0.50/2.00 + "Max 10.0 Lots" for >=10k) → fix to code values.

## Files to change
1. src/nexus_scalp/features/scalp_features.py (remove dead expr)
2. src/nexus_scalp/risk/risk_engine.py (align ctor defaults: max_allowed_lots 10.0, high_confidence_threshold 0.95 → config default)
3. src/nexus_scalp/training/walk_forward_trainer.py (fix stale TASK-1 log)
4. src/nexus_scalp/adapters/database/audit_repository.py (repair swarm damage)
5. agents/skill.md (§7 risk table → code truth)
6. tests/unit/test_risk_engine.py (risk regression tests: min/max risk, scaling, edge cases)
7. tests/unit/test_accounting_hedging.py (writer-path regression: log_order/log_execution/log_account_snapshot survive; reader paths work without _driver)
8. agents/bugs.md (append BUG-127: swarm audit_repository damage)
9. docs/forensic-docs/04_ISSUES_LEDGER.md (mark 4 issues fixed) + FORENSIC_FIX_REPORT.md

## Execution order
1. Send Telegram start (Persian).
2. Fix audit_repository.py (highest severity, live-path break) → py_compile → targeted tests.
3. Fix scalp_features.py dead expr → py_compile → test_scalp_features_forensic_bug082.py.
4. Fix walk_forward_trainer log → test_walk_forward_trainer.py.
5. Fix risk_engine ctor defaults → add risk tests → test_risk_engine.py + run risk-related.
6. Update agents/skill.md §7 table.
7. Append bugs.md BUG-127; update ledger.
8. Full beforePush gate (ruff check/format, mypy src, pytest tests/unit).
9. Commit per-step (NSE contract: commit EVERY coherent step), then FORENSIC_FIX_REPORT.md, final Telegram summary with full commit details.

## Safety
- No architecture changes; no hot-path blocking; no strategy/model/feature-contract changes.
- Use CRLF-safe editing (execute_code byte-exact) for CRLF files.
- Preserve parallel-agent working tree (never reset/clean/stash theirs).
# FORENSIC_FIX_REPORT.md

Date: 2026-08-20 · Agent: Hermes-Forensic-01 · Branch: main
Source: docs/forensic-docs/04_ISSUES_LEDGER.md (read-only forensic pass)

## 1. Fixed Issues

| # | Issue | Severity | Fix |
|---|-------|----------|-----|
| 1 | Dead expression scalp_features.py:879 (50% impulse-equilibrium, computed-and-discarded) | low | Removed statement + explanatory comment. Equilibrium ratio already captured by `ob_equilibrium_ratio`. |
| 2 | Risk contract mismatch: skill.md §7 said 0.50/2.00 lots; code truth is 0.02/0.10/1.00/min(10, vmax) | medium (doc contract) | skill.md tier table corrected to code values; RiskEngine ctor defaults aligned (`max_allowed_lots` 50→10 = HARD_MAX_LOTS parity, `high_confidence_threshold` 0.70→0.95 = AlgoConfig parity); 3 regression tests added. |
| 3 | Stale "TASK 1" diagnostic log in walk_forward_trainer (0=BUY,1=SELL,2=NO_TRADE vs real 0=NO_TRADE,1=BUY,2=SELL) | low (diagnostic) | Log now derives from `self.label_map` / `self.inverse_label_map`; stale header + hardcoded lines removed. |
| 4 | **BUG-127 (discovered during pass):** swarm commit c617c0f (TASK-21) formalized an incomplete driver refactor in audit_repository.py — `self._driver` undefined (6 sites → AttributeError on live order/execution/snapshot writes + readers), log_order/log_execution binding-count mismatch (12 cols vs 11 args) silently DROPPING every audit_orders/audit_executions row, log_account_snapshot lost ISO timestamp | **critical (live path)** | Restored pre-swarm (aa55115) behavior: per-method `sqlite3.connect(self._db_path)` + row_factory; ISO timestamps with matching `?` placeholders; `get_account_performance_metrics` with-block / return-after-compute. Ledger entry BUG-127 + regression test added. |

Also verified and kept (no change needed):
- Kelly: no Kelly criterion exists in the codebase; the brief's "Kelly" maps to the confidence-scaled risk-sizing block (risk_engine.py Part 7, confidence_scalar 0.5–1.2). Documented in FIX_PLAN.
- Risk source of truth: `RiskConfig.risk_per_trade_pct` (default 0.5, live.yaml 0.75 runtime); tier ceilings in `calculate_dynamic_volume` Step 6 / `get_clamped_position_size`; OrderManager `HARD_MAX_LOTS = 10.0`.

## 2. Files Changed

| File | Change |
|------|--------|
| src/nexus_scalp/adapters/database/audit_repository.py | BUG-127 repair (9bf7df5) + ruff format |
| agents/bugs.md | BUG-127 ledger entry appended (af28d3f) |
| src/nexus_scalp/features/scalp_features.py | dead expression removed (cdd7a45) |
| src/nexus_scalp/training/walk_forward_trainer.py | stale TASK-1 log fixed (76d3b50) |
| src/nexus_scalp/risk/risk_engine.py | ctor defaults aligned (cc6104c) |
| tests/unit/test_risk_engine.py | 3 risk contract tests added (cc6104c) |
| tests/unit/test_accounting_hedging.py | BUG-127 regression test added (c4b82b3) |
| agents/skill.md | §7 risk tier table corrected to code truth (cc6104c) |
| docs/forensic-docs/04_ISSUES_LEDGER.md | findings #2/#3/#9/#13 marked fixed + postscript |
| docs/forensic-docs/FIX_PLAN.md | plan artifact (new) |

## 3. Reason For Each Change

1. Dead code removal: no assignment/side-effect; keeping it would mislead maintainers into thinking it feeds a feature.
2. Risk contract: codes and docs must agree; code is the operating truth (defaults + HARD_MAX_LOTS + tests all align with 0.10/1.00 tiers). Ctor defaults were split-brain vs config/execution ceilings.
3. Trainer log: hardcoded class names contradicted the real label_map; diagnostics on a live training run were misleading.
4. BUG-127: the refactor was NOT intentional behavior change — it broke the trading audit hot path (silent data loss on every order) and undefined-attribute AttributeErrors. Verified via git blame (all damage in c617c0f), stash-merge root (aa55115 half-applied 4c9b148's driver work), and a failing binding-count error reproduced in pytest stderr.

## 4. Before / After Behavior

| Area | Before | After |
|------|--------|-------|
| audit_orders INSERT | SQL `DATETIME('now')` + 12 `?`/11 args → worker error, row dropped | 12 placeholders + 12 args (ISO timestamp) → row persisted |
| audit_executions INSERT | 8 cols, 7 args → dropped | 8 + 8 → persisted |
| get_account_performance_metrics | AttributeError `self._driver` / return-before-compute (always zeros) | computes win-rate/PF/drawdown from ledger (pre-swarm behavior) |
| reader methods (has_ledger_opened, count_ledger_opened_unclosed, get_recent_predictions, get_broker_deals_for_position) | AttributeError `self._driver` | work against `self._db_path` |
| RiskEngine ctor defaults | max_allowed_lots=50.0, high_confidence_threshold=0.70 | 10.0 (HARD_MAX_LOTS parity), 0.95 (config parity) |
| risk tier table (docs) | 0.50 / 2.00 | 0.10 / 1.00 (code truth) |
| trainer diagnostics log | hardcoded wrong class map + stale TASK-1 header | derived from label_map |
| scalp_features:879 | dead expression executed each tick | removed (identical outputs) |

## 5. Tests Executed

- tests/unit/test_accounting_core.py — PASS (incl. TestTradeForensics::test_trace_orders_attached, which FAILED pre-fix with "Incorrect number of bindings supplied")
- tests/unit/test_accounting_hedging.py — PASS (incl. new test_audit_writer_paths_bug127_regression)
- tests/unit/test_risk_engine.py — PASS (11 tests, incl. 3 new contract tests)
- tests/unit/test_scalp_features_forensic_bug082.py — PASS (20)
- tests/unit/test_walk_forward_trainer.py — PASS
- tests/unit/test_live_state_contract.py — PASS
- ruff check (6 changed files) — PASS
- ruff format (6 changed files) — PASS (3 reformatted, 3 already)
- mypy src (4 changed source files) — PASS ("Success: no issues found")
- Full tests/unit suite — running in background; results appended below.

## 6. Remaining Risks

- **Parallel-agent hazard:** working tree carries other agents' WIP (liquidity_engine.py, strategies/factory/*, ci.yml, accounting/core.py); those files are untouched by this pass. A later `git add -A` from another agent could absorb/override the audit_repository repair — verify with `git log --all -- <file>` if the file changes again.
- `live.yaml` runtime `risk_per_trade_pct: 0.75` differs from code default 0.5 — EXPECTED (runtime config overrides bootstrap; documented in runtime_config contract, BUG-126 fix).
- RiskEngine ctor default changes affect only callers that pass NO explicit max_allowed_lots/high_confidence_threshold; verified LiveEngine/OrderManager pass explicit values (no production behavior change).
- Ledger items #1 (aggregate_bars O(n) re-scan), #4 (ATR 1.50 magic constant), #5 (norm_rsi 16.66), #7/#8 (DB index debt, /api/news/keywords) are P3 technical debt — intentionally NOT changed in this pass (would be refactors beyond the verified-issues mandate).

## 7. Git Commits

| Commit | Summary |
|--------|---------|
| 9bf7df5 | Hermes-Forensic-01: repair swarm-damaged audit_repository (TASK-21 regression) |
| af28d3f | Hermes-Forensic-01: append BUG-127 to bug ledger |
| cdd7a45 | Hermes-Forensic-01: remove dead 50% impulse-equilibrium expression in scalp_features |
| 76d3b50 | Hermes-Forensic-01: fix stale TASK-1 diagnostic log in walk_forward_trainer |
| cc6104c | Hermes-Forensic-01: align risk contract - code truth 0.10/1.00 tiers, ctor defaults |
| c4b82b3 | Hermes-Forensic-01: add BUG-127 audit writer-path + reader regression test |
| (next) | ledger + format commit, then this report + push |

Final push details: see Terminal/GitHub status block at the end of the report (appended after push).
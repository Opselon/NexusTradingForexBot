# NexusTradingForexBot — Bug Ledger / Forensic History

## Purpose

This document serves as the authoritative Bug Ledger and Forensic History for the NexusTradingForexBot repository. It preserves verified bug discoveries, root cause analyses, implementation fixes, execution paths, regression protections, and architectural lessons learned across the project's lifecycle.

By keeping a structured, historical record of software failures and their mitigations, AI coding agents and human engineers can prevent regressions, understand non-obvious constraints, and ensure that previously solved problems are not re-introduced.

---

## Status Definitions

- **OPEN**: Unresolved bug currently under investigation or waiting for a fix.
- **INVESTIGATING**: Active forensic investigation in progress to establish root cause.
- **FIXED**: Corrective implementation applied to source code and validated locally.
- **VERIFIED**: Corrective implementation verified by automated unit/integration tests and CI/CD quality gates.
- **WONT_FIX**: Acknowledged issue or limitation determined to be acceptable or out of operational scope.
- **SUPERSEDED**: Obsolete issue made irrelevant by subsequent architectural refactoring or component removal.

---

## Severity Definitions

- **CRITICAL**: Threatens trading capital, leads to unhandled crashes, bypasses risk bounds, or corrupts live model inference.
- **HIGH**: Disrupts core execution loops, causes tick processing stalls, or degrades model performance severely.
- **MEDIUM**: Non-blocking feature degradation, performance inefficiencies, or legacy code ambiguity.
- **LOW**: Minor UI/logging discrepancies, non-critical warnings, or cosmetic code issues.

---

## Forensic Bug Ledger

---

## BUG-001 — Legacy 18D Script Feature Dimension Truncation Crash

- **Status**: VERIFIED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Historical Audit / CLI Execution
- **Fixed**: Historical Fix
- **Verified**: `tests/unit/test_train_model_cli.py` unit test suite & Pytest run

### Affected Components
- `src/cli/train_model.py` (CLI Training Orchestrator)
- `src/nexus_scalp/training/walk_forward_trainer.py`
- `src/nexus_scalp/models/scalp_net.py`

### Problem
When invoking the CLI training orchestrator `python -m cli.train_model`, the execution crashed with a tensor dimension mismatch exception (`ValueError: 50D feature contract violation`). The training script failed to pass valid dataset tensors to `ScalpNet` and `WalkForwardTrainer`.

### Root Cause
`src/cli/train_model.py` had a legacy implementation that hardcoded feature selection to an 18-element range (`range(18)` -> `feat_0` .. `feat_17`). This truncated the generated feature matrix and violated the mandatory 50-dimensional feature contract (`NUM_FEATURES = 50`) enforced across `ScalpFeatureEngine`, `WalkForwardTrainer`, and `ScalpNet`.

### Evidence
In `src/cli/train_model.py`, the selected feature columns were hardcoded as `[f"feat_{i}" for i in range(18)]` instead of `[f"feat_{idx}" for idx in range(WalkForwardTrainer.NUM_FEATURES)]`.

### Execution Path
`train_model.py::train()` -> `reconstruct_features_and_bars()` -> `WalkForwardTrainer.train_and_validate()` -> Tensor dimension mismatch exception on `ScalpNet` forward pass (`(Batch, 18)` vs required `(Batch, 50)`).

### Failure Scenario
Any attempt to run offline model retraining or walk-forward validation via the CLI command `python -m cli.train_model` failed immediately during feature vector preparation before training could commence.

### Impact
Offline model retraining pipeline was completely inoperable via the primary CLI entrypoint.

### Fix
Updated `src/cli/train_model.py` to construct, select, and map all 50 feature columns (`feat_0` .. `feat_49`) matching `WalkForwardTrainer.NUM_FEATURES = 50`.

### Regression Tests
Added unit tests in `tests/unit/test_train_model_cli.py`:
- `test_train_model_cli_50d_contract`: Verifies that feature extraction produces exactly 50 columns (`feat_0` .. `feat_49`).
- `test_train_model_validation_alignment`: Verifies that output DataFrames pass `WalkForwardTrainer._validate_training_frame`.

### Verification
Ran `pytest tests/unit/test_train_model_cli.py` and static type checks (`mypy`, `ruff`).

### Relevant Files
- `src/cli/train_model.py`
- `tests/unit/test_train_model_cli.py`
- `src/nexus_scalp/training/walk_forward_trainer.py`

### Architectural Lessons / Regression Guards
- CLI training scripts must always dynamically derive feature vector counts from the central domain/model contract (`WalkForwardTrainer.NUM_FEATURES`) rather than hardcoding feature index ranges.

---

## BUG-002 — Hot-Path Event Loop Synchronous Disk I/O Blocking

- **Status**: VERIFIED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Event Loop Latency Audit / Tick Stagnation Analysis
- **Fixed**: Historical Fix
- **Verified**: `tests/unit/test_rule_matrix.py` (`test_rule_matrix_ttl_throttling`)

### Affected Components
- `src/nexus_scalp/signals/rule_matrix.py` (`RuleMatrixEngine`)
- `src/nexus_scalp/signals/policy.py` (`SignalPolicy`)
- `src/nexus_scalp/web/server.py`
- `src/nexus_scalp/adapters/database/audit_repository.py`

### Problem
During live tick processing, the main async event loop experienced latency spikes and tick stagnation watchdog warnings under high tick volume.

### Root Cause
`RuleMatrixEngine.refresh_cache()` was invoked synchronously on every tick pulse during `SignalPolicy.evaluate_probabilities()` and `OrderLifecycleManager.manage_active_positions()`. `refresh_cache()` called `self.audit.get_trading_rules()`, executing a synchronous SQLite database query on the live event loop thread without caching or throttling.

### Evidence
Profiling log traces showed thread blocking during `get_trading_rules()` inside `refresh_cache()`, causing tick processing durations to exceed the 50ms pulse budget whenever disk I/O occurred.

### Execution Path
`LiveEngine._process_tick_pipeline()` -> `SignalPolicy.evaluate_probabilities()` -> `RuleMatrixEngine.refresh_cache()` -> `AuditRepository.get_trading_rules()` -> Synchronous SQLite `SELECT` query on live hot path.

### Failure Scenario
Under rapid tick delivery or high disk I/O load, synchronous database reads blocked the main asyncio thread, triggering tick stagnation watchdog alerts and delaying order execution/modifications.

### Impact
Hot-path tick latency degradation and risk of order slippage due to event-loop thread starvation.

### Fix
Implemented a 5-second Time-To-Live (TTL) cache inside `RuleMatrixEngine.refresh_cache(force=False, ttl_seconds=5.0)`. Synchronous database queries are suppressed during per-tick calls unless 5.0 seconds have elapsed or `force=True` is explicitly passed (e.g., when a user toggles a rule state via the FastAPI REST endpoint).

### Regression Tests
Added unit tests in `tests/unit/test_rule_matrix.py`:
- `test_rule_matrix_ttl_throttling`: Verifies that rapid successive calls to `refresh_cache()` within 5 seconds bypass SQLite reads and use in-memory cached rules.

### Verification
Ran `pytest tests/unit/test_rule_matrix.py` and `beforePush.sh` quality pipeline.

### Relevant Files
- `src/nexus_scalp/signals/rule_matrix.py`
- `src/nexus_scalp/web/server.py`
- `tests/unit/test_rule_matrix.py`

### Architectural Lessons / Regression Guards
- Never perform synchronous database or file I/O operations directly on the live tick hot path. Use TTL in-memory caching or asynchronous background worker queues.

---

## BUG-003 — Dead Legacy Order Manager File Ambiguity & Overhead

- **Status**: VERIFIED
- **Severity**: MEDIUM
- **Confidence**: HIGH
- **Discovered**: Repository Forensic Audit
- **Fixed**: Historical Fix
- **Verified**: `tests/unit/test_order_manager_audit.py`

### Affected Components
- `src/nexus_scalp/features/order_manager.py` (Legacy file)
- `src/nexus_scalp/execution/order_manager.py` (Active production implementation)
- `NexusTradingForexBot.pyproj`

### Problem
The codebase contained two files named `order_manager.py`: `src/nexus_scalp/features/order_manager.py` (234 lines) and `src/nexus_scalp/execution/order_manager.py` (4573 lines). This duplicate filename caused developer confusion and maintenance risk.

### Root Cause
An obsolete legacy implementation was left behind in `src/nexus_scalp/features/order_manager.py` during prior refactoring.

### Evidence
A repository-wide AST import audit confirmed 0 active code imports and zero dynamic loading paths for `src/nexus_scalp/features/order_manager.py`. Active production order lifecycle execution was strictly handled by `src/nexus_scalp/execution/order_manager.py`.

### Execution Path
N/A (File was unreferenced and orphaned).

### Failure Scenario
Engineers or AI coding agents could inadvertently import or update the dead feature file instead of the active execution order manager, leading to silent non-operational code changes.

### Impact
Increased cognitive load, confusion regarding production order management logic, and potential maintenance errors.

### Fix
Deleted `src/nexus_scalp/features/order_manager.py`, removed its compile item reference from `NexusTradingForexBot.pyproj`, and added forensic audit unit tests.

### Regression Tests
Added unit tests in `tests/unit/test_order_manager_audit.py`:
- `test_legacy_order_manager_deleted`: Confirms physical removal of `src/nexus_scalp/features/order_manager.py`.
- `test_no_imports_of_legacy_order_manager`: Verifies repo-wide absence of legacy import statements.
- `test_active_order_manager_imported`: Confirms `src/nexus_scalp/execution/order_manager.py` is active and functional.

### Verification
Ran `pytest tests/unit/test_order_manager_audit.py` and full unit test suite.

### Relevant Files
- `src/nexus_scalp/features/order_manager.py` (deleted)
- `src/nexus_scalp/execution/order_manager.py`
- `tests/unit/test_order_manager_audit.py`
- `NexusTradingForexBot.pyproj`

### Architectural Lessons / Regression Guards
- Orphaned legacy files should be removed promptly with automated AST import tests to prevent codebase duplication and ambiguity.

---

## BUG-004 — Cold-Start Higher Timeframe (HTF) Feature Neutral Default Fallback

- **Status**: VERIFIED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Live Engine Cold-Start Audit
- **Fixed**: Historical Fix
- **Verified**: `tests/unit/test_htf_warmup_gate.py`

### Affected Components
- `src/nexus_scalp/application/live_engine.py` (`LiveEngine`)
- `src/nexus_scalp/features/scalp_features.py` (`ScalpFeatureEngine`)
- `src/nexus_scalp/adapters/mt5/mt5_adapter.py`

### Problem
Upon cold-starting `LiveEngine`, live tick pulses were processed immediately before a sufficient number of completed H1 and H4 historical bars (14 periods required for ATR lookbacks) had been fetched or aggregated.

### Root Cause
Lack of an explicit startup warmup gate. When insufficient HTF bars were present in memory, `ScalpFeatureEngine` returned neutral fallback defaults (`0.0`) for multi-timeframe indicators (`htf_h4_trend`, `htf_h1_momentum`), causing `ScalpNet` model inference to execute on incomplete feature data.

### Evidence
Log traces showed `ScalpNet` model inference executing during the first few seconds of startup with default neutral HTF values before historical bars were fully hydrated.

### Execution Path
`LiveEngine.start()` -> First tick received -> `_process_tick_pipeline()` -> `compute_from_bars()` -> Incomplete HTF bar list -> Neutral fallback vector (`0.0`) -> `ScalpNet` inference allowed.

### Failure Scenario
Trade proposals generated during the first few seconds of system cold start were evaluated against inaccurate neutral HTF feature values, potentially taking trades against the true higher-timeframe trend.

### Impact
Unintended trade execution during startup initialization due to incomplete higher-timeframe context.

### Fix
Implemented an explicit HTF Warmup Gate state machine (`WARMING_UP -> READY` or `SAFE_NOT_READY`) inside `LiveEngine`:
- Bootstraps historical H1 and H4 bars asynchronously on startup (3500 M1 bars or direct MT5 queries).
- Requires a minimum of 14 completed H1 bars and 14 completed H4 bars before enabling neural inference.
- Gates `ScalpNet` model inference (`[INFERENCE] BLOCKED reason=HTF_WARMUP_INCOMPLETE`) until all HTF features pass validation.

### Regression Tests
Added 11 unit tests in `tests/unit/test_htf_warmup_gate.py`:
- Verified warmup state transition logic, bar requirements, non-blocking asynchronous hydration, inference blocking during `WARMING_UP`, and console telemetry logs.

### Verification
Ran `pytest tests/unit/test_htf_warmup_gate.py`.

### Relevant Files
- `src/nexus_scalp/application/live_engine.py`
- `src/nexus_scalp/adapters/mt5/mt5_adapter.py`
- `tests/unit/test_htf_warmup_gate.py`

### Architectural Lessons / Regression Guards
- Deep neural inference pipelines depending on multi-timeframe features must strictly enforce a cold-start Warmup Gate that blocks inference until all lookback history requirements are met.

---

## BUG-005 — Inverse Class Weight Explosion & Model Validation Collapse

- **Status**: VERIFIED
- **Severity**: CRITICAL
- **Confidence**: HIGH
- **Discovered**: Training Log Trace Diagnosis
- **Fixed**: Historical Fix
- **Verified**: `tests/unit/test_walk_forward_trainer.py` & `PROGRESS.md`

### Affected Components
- `src/nexus_scalp/training/walk_forward_trainer.py` (`WalkForwardTrainer`)
- `src/nexus_scalp/models/scalp_net.py`

### Problem
During online fine-tuning and walk-forward training folds, loss gradients exploded (`train_loss = 6.57`), causing validation accuracy to collapse to `15.1%` with a `98.9%` skewed directional bias (`is_healthy = False`), triggering model checkpoint rollbacks.

### Root Cause
The inverse class frequency weighting formula produced unclamped minority class weights with weight factors up to `10.0` (`weights = [1.61, 10.0, 10.0]`). Multiplying loss gradients by $10.0$ caused training loss explosion during backpropagation, skewing neural outputs toward a single dominant class.

### Evidence
Log traces recorded `weights_4d=[1.61, 10.0, 10.0]`, `train_loss=6.57`, `validation_accuracy=0.151`, and severe directional bias.

### Execution Path
`WalkForwardTrainer._train_epoch()` -> `_build_class_weights()` -> Unclamped inverse frequency calculation -> Loss weight multiplier of `10.0` -> Gradient explosion -> Network output collapse -> Checkpoint rejection.

### Failure Scenario
Online fine-tuning failed consistently, forcing `LiveEngine` to repeatedly reject newly trained model weights and fall back to older baseline weights.

### Impact
Inability to adapt model weights online to shifting micro-tick market regimes.

### Fix
Refactored class weight calculation in `WalkForwardTrainer._build_class_weights()`:
1. Applied bounded clamping: $W_c = \text{clamp}\left(\frac{N_{\text{total}}}{3.0 \times (N_c + 1.0)}, \text{min}=0.5, \text{max}=2.0\right)$.
2. Normalized weights so their mean equals exactly $1.0$ ($W.\text{mean}() == 1.0$).
3. Calibrated online fine-tuning default parameters to 3 epochs and $5\times 10^{-5}$ learning rate.

### Regression Tests
Unit tests in `tests/unit/test_walk_forward_trainer.py`:
- Verified bounded class weight generation within $[0.5, 2.0]$ and mean normalization.

### Verification
Ran `pytest tests/unit/test_walk_forward_trainer.py`.

### Relevant Files
- `src/nexus_scalp/training/walk_forward_trainer.py`
- `tests/unit/test_walk_forward_trainer.py`
- `PROGRESS.md`

### Architectural Lessons / Regression Guards
- Class weighting in neural loss functions must always be strictly bounded and normalized to prevent gradient explosions on imbalanced financial datasets.

---

## BUG-006 — Toxicity Calculation Asymptote Overflow

- **Status**: VERIFIED
- **Severity**: MEDIUM
- **Confidence**: HIGH
- **Discovered**: Code Inspection / Adaptive Exit Engine Audit
- **Fixed**: Historical Fix
- **Verified**: `src/nexus_scalp/execution/order_manager.py` (Line 2093)

### Affected Components
- `src/nexus_scalp/execution/order_manager.py` (`OrderLifecycleManager`)

### Problem
During extreme spread expansion or order flow imbalance spikes, toxicity calculation in position management risked division-by-zero or asymptotic overflow.

### Root Cause
Unbounded division when calculating order flow toxicity metrics under near-zero denominator conditions.

### Evidence
Code comment in `order_manager.py`: `# FIXED ASYMPTOTE BUG: Bounded toxicity calculation`.

### Execution Path
`OrderLifecycleManager.evaluate_position_health()` -> Microstructure toxicity calculation -> Division by near-zero denominator -> Floating point infinity or overflow exception.

### Failure Scenario
Unexpected `ZeroDivisionError` or `OverflowError` during position health evaluation on rapid volatility spikes, interrupting position protection logic.

### Impact
Potential failure to execute early risk exits or trailing stops during toxic order flow conditions.

### Fix
Bounded toxicity calculation with an explicit epsilon ($\epsilon = 1e-8$) denominator floor and clamped upper bound.

### Regression Tests
`tests/unit/test_order_lifecycle.py` and `tests/unit/test_adaptive_position_management.py`.

### Verification
Ran `pytest tests/unit/test_adaptive_position_management.py`.

### Relevant Files
- `src/nexus_scalp/execution/order_manager.py`
- `tests/unit/test_adaptive_position_management.py`

### Architectural Lessons / Regression Guards
- All mathematical formulas evaluating financial ratios or market toxicity must include explicit epsilon safeguards against division by zero.

---

## BUG-007 — Polars Bitwise Not Boolean Masking Exception

- **Status**: VERIFIED
- **Severity**: MEDIUM
- **Confidence**: HIGH
- **Discovered**: Polars Pipeline Refactoring
- **Fixed**: Historical Fix
- **Verified**: `src/nexus_scalp/training/walk_forward_trainer.py` (Line 832)

### Affected Components
- `src/nexus_scalp/training/walk_forward_trainer.py`

### Problem
Attempting to filter Polars DataFrames using standard Python `not` operator raised a `ComputeError` or produced invalid boolean masks during embargo and purging window calculations.

### Root Cause
Polars expression syntax requires bitwise tilde operator (`~`) rather than Python logical `not` for expression negation.

### Evidence
Code comment in `walk_forward_trainer.py`: `out = out.filter(~pl.col("is_purged"))  # <-- FIXED: Bitwise NOT for Polars`.

### Execution Path
`WalkForwardTrainer._apply_purging()` -> `df.filter(not pl.col("is_purged"))` -> Polars `ComputeError`.

### Failure Scenario
Purged walk-forward fold generation crashed during validation dataset construction.

### Impact
Walk-forward dataset splitting failure during model training runs.

### Fix
Replaced logical `not` with bitwise tilde `~pl.col("is_purged")`.

### Regression Tests
`tests/unit/test_walk_forward_trainer.py`.

### Verification
Ran `pytest tests/unit/test_walk_forward_trainer.py`.

### Relevant Files
- `src/nexus_scalp/training/walk_forward_trainer.py`
- `tests/unit/test_walk_forward_trainer.py`

### Architectural Lessons / Regression Guards
- When constructing filter conditions in Polars DataFrames, always use bitwise operators (`~`, `&`, `|`) instead of Python logical operators (`not`, `and`, `or`).


---

## BUG-008 — AccountingCore Strategy Attribution Join Keyed on Empty Column

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Phase 08 Continuation Forensic Audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_accounting_core.py` and `tests/integration/test_accounting_api.py`

### Affected Components
- `src/nexus_scalp/accounting/core.py` (`AccountingCore._attach_identity`)
- `src/nexus_scalp/experience/ledger.py` / `intelligence.py` (decision + outcome persistence)

### Problem
`AccountingCore._attach_identity()` joined closed trades to their Experience
decision using `audit_experiences.execution_id = trade.ticket`. Because the
experience row is written at DECISION time (before a broker ticket exists) and
is IMMUTABLE (nothing in the codebase ever issues an UPDATE against
`audit_experiences`), that column is ALWAYS empty. The join therefore matched
nothing, and every trade silently lost its strategy/model/schema attribution.

### Root Cause
The identity chain in the actual schema runs through the OUTCOME table:

    audit_ledger.ticket = audit_experience_outcomes.execution_id
    audit_experience_outcomes.idempotency_key = audit_experiences.idempotency_key

The experience row's `execution_id` is a placeholder by design (decision-time
insert), while the broker ticket only ever appears on the outcome row. Joining
the ledger directly to `audit_experiences.execution_id` can never resolve.

### Evidence
- `ExperienceLedger.record_experience()` writes `execution_id` from the
  decision-time record (empty).
- `ExperienceIntelligenceEngine.record_trade_outcome()` writes the broker
  ticket into `audit_experience_outcomes.execution_id`.
- grep confirmed zero UPDATE statements targeting `audit_experiences`.
- Live-data check on `artifacts/audit.db`: strategy contributions returned an
  empty list even with 36 closed trades, because no ledger row could be joined.

### Impact
Strategy attribution, model provenance, feature-schema provenance, loss
attribution with strategy context, and the dashboard Strategy Attribution
panel would all silently report "no evidence" despite the ledger containing
fully-attributable trades.

### Fix
Rewrote `AccountingCore._attach_identity` to join through the outcome table
(see source diff: audit_experience_outcomes o JOIN audit_experiences e ON
e.idempotency_key = o.idempotency_key WHERE o.execution_id IN (...)).

### Regression Tests
- `tests/unit/test_accounting_core.py::TestStrategyAttribution::test_trade_linked_to_strategy_via_outcome`
- `tests/unit/test_accounting_core.py::TestStrategyAttribution::test_strategy_contributions_aggregate`
- `tests/integration/test_accounting_api.py::TestAccountingApi::test_strategies_endpoint_linked`
- `tests/integration/test_accounting_api.py::TestAccountingApi::test_trade_forensics_endpoint`

### Verification
All regression tests green; strategy attribution now resolves real strategy
identity from the outcome table.

### Relevant Files
- `src/nexus_scalp/accounting/core.py`
- `tests/unit/test_accounting_core.py`
- `tests/integration/test_accounting_api.py`

### Architectural Lessons / Regression Guards
- Immutable decision rows cannot carry runtime-only identifiers (broker
  tickets). Any cross-table identity join MUST use the outcome/event table as
  the bridge, never the immutable decision row.
- When auditing a join, verify against the ACTUAL schema write paths, not the
  column's declared intent.
## BUG-009 — ExperienceRetriever Mutated Caller's Confluence Token List

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Phase 08 Continuation Forensic Audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_experience_intelligence.py`

### Affected Components
- `src/nexus_scalp/experience/retriever.py` (`ExperienceRetriever.build_confluence_fingerprint`)

### Problem
`build_confluence_fingerprint` appended tokens to the caller's `confluence_tokens`
list (`tokens.update(...)` on the shared list), so repeated calls with a shared
list produced a drifting fingerprint and a different `strategy_id` for identical
market state.

### Root Cause
The function worked on the caller's list object rather than a local copy.

### Fix
The function now builds a `set(confluence_tokens or ())` local copy and never
mutates the caller's list.

### Regression Guards
- `build_confluence_fingerprint` must never mutate its caller's list.

---

## BUG-010 — Experience Gate Gated Every Non-NO_TRADE Action

- **Status**: FIXED
- **Severity**: CRITICAL
- **Confidence**: HIGH
- **Discovered**: Phase 08 Continuation Forensic Audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_experience_intelligence.py`

### Affected Components
- `src/nexus_scalp/experience/intelligence.py` (`ExperienceIntelligenceEngine`)

### Problem
The first Phase 08 revision gated every non-NO_TRADE action, so a retired
strategy could suppress a protective CLOSE_POSITION / PARTIAL_CLOSE /
MODIFY_SL_TP / CANCEL_ORDER — a capital-safety failure.

### Root Cause
The gate scope was broader than the intended "only entry proposals are gated".

### Fix
The gate now gates ONLY entry actions (`GATED_ENTRY_ACTIONS`); position-management
actions pass through untouched.

### Regression Guards
- `GATED_ENTRY_ACTIONS` must never include a position-management action.

---

## BUG-011 — Position Lifecycle Giveback Required Non-Negative Floating PnL

- **Status**: FIXED
- **Severity**: LOW
- **Confidence**: HIGH
- **Discovered**: Phase 09 Development (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_intelligence_phase09.py::TestPositionLifecycle::test_profit_giveback_detection`

### Affected Components
- `src/nexus_scalp/intelligence/lifecycle.py` (`PositionLifecycleTracker.observe_position`)

### Problem
The `POSITION_PROFIT_GIVEBACK` event required `snapshot.floating_pnl >= 0.0`. A
position that ran to +$150 and was still closed at +$20 correctly triggers; but
a position that ran to a loss of -$6 (after peaking at +$20) never produced the
giveback event, even though an 87% giveback of peak profit occurred.

### Root Cause
The giveback classification conflated "still in profit" with "gave back profit";
a deep adverse swing after a profit peak is exactly the behavior the event must
record, yet the `floating_pnl >= 0.0` guard suppressed it.

### Fix
The giveback event now fires whenever `peak_profit > 0.0` and the recorded
`profit_giveback_pct` clears the notice threshold, regardless of whether the
position is still net positive. The `profit_giveback_pct` is derived from the
recorded peak vs current excursion, which is the objective signal.

### Regression Guards
- Giveback detection must measure surrender of peak profit, not the sign of
  current floating PnL.

---

## BUG-012 — Phase 09 Gate WARN Tier Unreachable Without Evidence Path

- **Status**: FIXED
- **Severity**: MEDIUM
- **Confidence**: HIGH
- **Discovered**: Phase 09 Development (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_intelligence_phase09.py::TestGate::test_warn_tier_on_elevated_drawdown`

### Affected Components
- `src/nexus_scalp/intelligence/gate.py` (`PreTradeIntelligenceGate`)

### Problem
The initial `SuitabilityTier` subclassing of `ExperienceAction` raised a
`TypeError: <enum 'SuitabilityTier'> cannot extend <enum 'ExperienceAction'>`
because StrEnum cannot be extended at runtime with new members. This also left
the WARN tier logic unreachable in the first draft (the evidence path never ran).

### Root Cause
Runtime enum extension is not allowed for `StrEnum` subclasses with new members.

### Fix
`SuitabilityTier` is now a standalone `StrEnum` and the evidence-based verdict
logic is factored into `_evaluate_with_evidence()` so it is directly testable.

### Regression Guards
- Do not subclass a `StrEnum` with additional members; use a standalone enum.

---

## BUG-013 — Hold Score Pegged at 97-100 During Deep Drawdown (Linear Penalty + Bonus Masking)

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Runtime log autopsy (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_log_autopsy_fixes.py::TestHoldScoreDegradation` and
  `tests/unit/test_rule_matrix.py::test_dynamic_hold_score_calculation`

### Affected Components
- `src/nexus_scalp/execution/order_manager.py::_calculate_hold_value_score`

### Problem
During heavy drawdown (e.g. ticket at peak loss -$80.60 against a ~$100 risk
budget), `hold_score` remained pegged at 97-100/100. The engine therefore held
the losing trade until the hard time horizon expired, then fired a sudden
`[HYSTERESIS BYPASS - EMERGENCY TRANSITION]` instead of de-risking gracefully.

### Root Cause
1. `DRAWDOWN_PENALTY` was linear (`ratio * 40`, capped at 40) so a 50%-of-risk
   drawdown only removed ~8-20 points.
2. `TREND_ALIGNMENT_BONUS (+10)` was applied UNCONDITIONALLY, cancelling the
   drawdown penalty whenever the higher-timeframe trend was aligned.
3. `PROFIT_SHIELD_SCORE_FLOOR_ACTIVE` (`max(85, score)`) was keyed on
   `price_current vs price_open` rather than actual floating PnL, so a whipsaw
   could push a genuinely losing position to an 85+ floor.

### Fix
- Convex drawdown penalty: `80 * ratio^1.5` (capped at 80) - a 50% drawdown now
  removes ~28 points, a 90% drawdown ~68.
- Trend bonus is suppressed whenever the drawdown ratio is >= 0.30.
- Profit-shield floor now uses `pos.profit >= 0.0` and is disabled underwater.

### Regression Guards
- A 50% drawdown must drive `hold_score` below ~60 (was ~97-100).

---

## BUG-014 — Profit Giveback Closed Micro-Scalps at Break-Even (Noise Trip)

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Runtime log autopsy (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_log_autopsy_fixes.py::TestTieredGivebackProtection`

### Affected Components
- `src/nexus_scalp/execution/order_manager.py::evaluate_profit_giveback`,
  `_tiered_giveback_floor`, `_evaluate_candidate_state`

### Problem
Trades like ticket #152486259094 (peak +$21.06, closed +$4.32 = 20.5% retention)
and #152486296273 (peak +$23.12, closed +$1.36 = 5.9%) were cut at net $0.00.
On 0.5-0.7 lots of XAUUSD a ~$20 peak is only 3-4 pips, so normal bid/ask noise
tripped the flat 30% retention floor and killed runners at break-even.

### Root Cause
The giveback protection armed at a flat `PROFIT_GIVEBACK_PEAK_USD = $20` and
used a flat `PROFIT_GIVEBACK_MIN_RETENTION = 0.30` floor, regardless of the
peak's size in R. A 3-pip scalp has no meaningful cushion to lose before the
floor fires.

### Fix
Introduced a TIERED retention floor derived from the peak's R multiple
(`_tiered_giveback_floor`):
- peak < 0.5R  -> protection DISARMED (micro-profit noise zone)
- 0.5R-1.0R    -> retain >= 40%
- 1.0R-1.5R    -> retain >= 50%
- >= 1.5R      -> retain >= 70%

### Regression Guards
- A <0.5R peak pulled back to 20% retention must NOT be closed.
- A >1.5R peak at <70% retention MUST still be closed.

---

## BUG-015 — Cold-Start Fallback Scaler Never Persisted Until First Accepted Fine-Tune

- **Status**: FIXED
- **Severity**: MEDIUM
- **Confidence**: HIGH
- **Discovered**: Runtime log autopsy (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_log_autopsy_fixes.py::TestScalerColdStartPersistence`

### Affected Components
- `src/nexus_scalp/training/walk_forward_trainer.py::fine_tune_online`

### Problem
On every cold start `model.scaler.npz` was missing; the trainer fitted a
fallback scaler on a tiny (~196 sample) non-representative buffer but did NOT
persist it. When the quality gate rejected the fine-tune (which it did on every
bootstrap run), the scaler was never written, so every reboot re-fitted on a
different tiny buffer - destabilising the live feature distribution between
restarts.

### Fix
The cold-start fallback scaler is now persisted to disk immediately after
fitting (`_save_scaler(scaler)`), regardless of whether the fine-tune later
passes the quality gate.

### Regression Guards
- `_get_scaler_path()` must exist after the first cold-start fit.

---

## BUG-016 — Mono-Class Model Collapse Never Recovered (Broken Baseline Served Indefinitely)

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Runtime log autopsy (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `src/nexus_scalp/application/live_engine.py::_reinitialize_collapsed_model`
  (smoke-tested via `tests/integration/test_intelligence_api.py`)

### Affected Components
- `src/nexus_scalp/application/live_engine.py`

### Problem
Diagnostics showed `class_dist=BUY 0.0% | SELL 100.0% | NO_TRADE 0.0%` - the
model had collapsed to a single class. The fine-tuning quality gate rejected
every bootstrap run and rolled back to the SAME collapsed baseline, so the
engine served a permanently broken model.

### Fix
Added `_detect_model_collapse` + `_reinitialize_collapsed_model`: after the
bootstrap diagnostics, if the (possibly rolled-back) model shows >= 85%
mono-class dominance on an active class, the model is re-initialized with fresh
weights atomically under `_bundle_lock`. The experience ledger and strategy
memory are untouched.

### Regression Guards
- A 100% mono-class model must be re-initialized rather than served.

---

## BUG-017 — Breakeven SL Did Not Include Live Spread in Stop-Distance Clearance

- **Status**: FIXED
- **Severity**: MEDIUM
- **Confidence**: HIGH
- **Discovered**: Runtime log autopsy (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_log_autopsy_fixes.py::TestBreakevenClearance`

### Affected Components
- `src/nexus_scalp/execution/order_manager.py::apply_breakeven_lock`

### Problem
`BREAKEVEN DEFERRED: market pulled back, SL would cross market price` loops
occurred because the breakeven clearance used only the broker STOP_LEVEL
distance, which can be smaller than the live spread on XAUUSD. A breakeven SL
placed at exactly STOP_LEVEL distance can still be rejected (or crossed by the
fill).

### Fix
`effective_freeze_gap` now includes the live spread:
`max(min_stop_gap, 0.35) + max(live_spread, 0.0)`. The modification is deferred
until price gives enough room rather than firing a guaranteed-reject request.

### Regression Guards
- A breakeven SL must stay at least (STOP_LEVEL + spread) from the market.

---

## BUG-018 — Emergency Exit Suppressed on First Observation of a Restarted Split Leg

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Log-autopsy fix development (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_log_autopsy_fixes.py::TestSplitOrderSync`

### Affected Components
- `src/nexus_scalp/execution/order_manager.py::transition_state_with_hysteresis`,
  `_close_sibling_legs`

### Problem
Two tickets of one split dispatch frequently desynchronized: one entered
`LOSS_HARD_EXIT` while the sibling stayed in `LOSS_RECOVERY_CANDIDATE`, leaving
the position half-closed. On a restart, the first observation of an already-old
leg with exhausted recovery budget was ALSO debounced into the safe neutral
state (`LOSS_RECOVERY_CANDIDATE`) because the emergency bypass only ran when a
current state already existed.

### Fix
1. `_close_sibling_legs`: when a leg is emergency-closed, sibling tickets sharing
   the same originating order_id are closed together.
2. `transition_state_with_hysteresis` now honors `LOSS_HARD_EXIT` /
   `PROFIT_GIVEBACK_CRITICAL` even on the FIRST observation, so a restart can
   never silently "un-de-risk" an already-exhausted split leg.

### Regression Guards
- An emergency close of one split leg must close the sibling leg.
- Unrelated tickets must never be cross-closed.


## BUG-019 — Legacy Metrics Calculator Reversed Commission/Swap Sign (Duplicate Engine Drift)

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Phase 08 Continuation Forensic Audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_accounting_hedging.py` (assertion recomputed to the
  correct 3.40 profit factor) + full unit suite

### Affected Components
- `src/nexus_scalp/adapters/database/audit_repository.py::get_account_performance_metrics`
- `src/nexus_scalp/web/server.py::get_account_summary` (consumed it)

### Problem
`get_account_performance_metrics` computed `net = pnl + commission + swap`, i.e. it
ADDED commission and swap back to gross PnL. Commission and swap are COSTS; the
canonical `log_ledger_closed` and `AccountingCore.normalize_trade_row` both compute
`net = gross - commission - swap`. The legacy calculator therefore inflated profits
and disagreed with the canonical accounting core - a second, wrong calculation
engine contradicting the ONE-engine invariant. `/api/account/summary` served those
inflated numbers.

### Root Cause
The sign convention used when the ledger was first written (commission/swap as
positive magnitudes to subtract) was not applied in this calculator, and its
`commission` column reads the RAW signed value passed by `log_ledger_closed`
(e.g. -2.0), so the formula must use `abs(commission)` exactly like
`normalize_trade_row` does.

### Evidence
- `tests/unit/test_accounting_hedging.py` seeded commissions as `-2.0`/`-1.0`
  and asserted profit_factor 3.40 (the CORRECT math); the buggy calculator
  returned 3.61 (inflated), failing the assertion.

### Fix
`net_pnl = pnl - abs(commission) - swap` (swap kept signed - a credit is a credit).

### Regression Guards
- `get_account_performance_metrics` must agree with `AccountingCore` period
  reports within float tolerance for the same ledger rows.
- The hedging test's 3.40 profit-factor assertion is now the regression guard.

---

## BUG-020 — Dashboard/API Served Synthetic Placeholder Numbers

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Phase 08 Continuation Forensic Audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/integration/test_accounting_api.py::TestWorkerWithEngine::test_account_summary_never_serves_synthetic_numbers`

### Affected Components
- `src/nexus_scalp/web/server.py::get_account_summary` (`/api/account/summary`)
- `src/nexus_scalp/web/server.py::get_system_state` (`/api/status` account block)
- `Web/app.js` account rendering (null-safe)

### Problem
`/api/account/summary` returned hardcoded `balance=10000.00`, `equity=10000.00`,
`win_rate=0.0`, `profit_factor=0.0` placeholders whenever the adapter could not be
read or no history existed, and `/api/status` defaulted `account_data` to
`balance=10000.00` / `win_rate=78.5`. This violated the Phase 08 no-synthetic-
numbers invariant on LIVE dashboard endpoints and made failures indistinguishable
from genuine flat results.

### Root Cause
Legacy pre-Phase-08 endpoint bodies relied on default constants instead of the
canonical `AccountingCore` facade; the Phase 08 refactor added the facade but did
not rewire these legacy endpoints.

### Fix
- `/api/account/summary` now reads `AccountingCore.live_state()` + ledger-backed
  totals; unavailable fields are `None`, never placeholders.
- `/api/status` account block defaults to `available=False` with `None` fields and
  reads the real win rate from the canonical core when an engine exists.
- `Web/app.js` renders `n/a` for null account fields instead of crashing on
  `.toFixed()` of `null`/`NaN`.

### Regression Guards
- No endpoint may return a hardcoded balance/win-rate constant. Any fake-zero
  dashboard value is a regression.
- `test_account_summary_never_serves_synthetic_numbers` asserts real values when
  the adapter is up and None fields when the adapter raises.

---

## BUG-021 — Forensic Trace Quality Join Reused the BUG-008 Empty-Column Trap

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Phase 08 Continuation Forensic Audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_accounting_core.py::TestForensicQualityJoin`

### Affected Components
- `src/nexus_scalp/accounting/core.py::_attach_experience_detail`

### Problem
`_attach_experience_detail` (forensic trade trace quality section) joined the
outcome table with `WHERE e.execution_id = ?` where `e` is `audit_experiences`.
That column is ALWAYS empty by design (immutable decision row written before a
broker ticket exists - see BUG-008). Every forensic trace therefore silently
reported `NO_EXPERIENCE_OUTCOME` and carried an empty quality decomposition even
for fully-attributable trades, and behavioral flags never reached the dashboard.

### Root Cause
The BUG-008 join trap was applied a second time in a different function
(`_attach_identity` was fixed, `_attach_experience_detail` was not).

### Fix
Rewrote the join to go through the outcome table, matching `_attach_identity`:
`WHERE o.execution_id = ?` (`o` = `audit_experience_outcomes`).

### Regression Guards
- Forensic traces for outcome-linked trades must carry the decomposition columns
  and behavioral flags (regression test asserts strategy/entry/execution/
  management/exit quality and `EARLY_EXIT` round-trip).
- Grep guard: no `WHERE e.execution_id = ?` may exist against
  `audit_experiences` anywhere in `src/`.

---

## BUG-022 — intelligence_worker_state Table Was Dead (Checkpoint Never Written)

- **Status**: FIXED
- **Severity**: MEDIUM
- **Confidence**: HIGH
- **Discovered**: Phase 08 Continuation Forensic Audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_intelligence_phase09.py::TestWorkerIsolation::test_worker_checkpoint_persists_across_restart`

### Affected Components
- `src/nexus_scalp/intelligence/worker.py` (`IntelligenceWorker`)
- `src/nexus_scalp/adapters/database/audit_repository.py` (schema)

### Problem
The schema created `intelligence_worker_state`, but NO code ever wrote to or read
from it. The worker's docstring claimed "a checkpoint is recorded so nothing is
rebuilt redundantly", yet a restart simply redid the full cycle from zero state -
the restart-safety story was documentation-only.

### Root Cause
Checkpoint persistence was specified but never implemented when the worker was
introduced.

### Fix
`IntelligenceWorker.start()` now loads the checkpoint (`_load_checkpoint`) and
`stop()` persists it (`_save_checkpoint`), restoring `cycle_count` and the last
autopsy count across restarts. Reads/writes are failure-isolated (a missing table
simply means first run).

### Regression Guards
- A fresh worker instance must restore `cycle_count >= 1` after a prior
  start/tick/stop against the same database.

---

## BUG-023 — No Real Market-Data Backtest / Validation Layer (Phase 09 gap)

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Phase 09B Forensic Audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_research_phase09b.py` (45 tests),
  `tests/integration/test_research_api.py` (7 tests)

### Affected Components
- `src/nexus_scalp/intelligence/evolution.py` (`validate_candidate` was only a
  bounded recording API, not a market-data backtest)
- Missing: deterministic backtest, walk-forward, OOS gate, robustness engine,
  multi-dimension scoring, strategy registry.

### Root Cause
The prior Phase 09 delivered candidate *discovery* and operator-gated
*promotion*, but the actual market-data backtest harness, walk-forward, OOS,
robustness and scoring engines were never implemented. `validate_candidate`
only recorded an expectancy/sample count supplied by the caller - it did not
evaluate the candidate over historical data, so a "validated" candidate could
not be distinguished from an unbacktested hypothesis on statistical evidence.

### Fix
Built a full `src/nexus_scalp/research/` subsystem:
- causal-safe dataset builder over the immutable experience ledger,
- temporal splits + walk-forward with purge/embargo,
- deterministic friction-aware backtest (`BacktestEngine`),
- hard OOS gate (OOS failure ⇒ REJECTED),
- robustness engine (spread/slippage/latency stress, degradation measured),
- explainable multi-dimension `StrategyScore` with small-sample protection,
- content-addressed `StrategyCandidate` versioning (modified strategy = new
  version, old records immutable),
- `StrategyRegistry` + `research_runs` + `research_worker_state` tables,
- isolated `ResearchWorker` + 7 REST endpoints + LiveEngine wiring.

### Regression Guards
- Candidate can never bypass RiskEngine / OrderManager / MT5 (tested).
- Pipeline never promotes a candidate to ACTIVE automatically (tested).
- OOS failure forces REJECTED regardless of in-sample/win-rate (tested).
- Small samples never receive high confidence (tested).
- Modified strategy gets a new version; old validation record stays intact
  (tested).

---

## BUG-024 — No Controlled Training / Champion Protection Boundary (Phase 10 gap)

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Phase 10 Forensic Audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_model_lifecycle_phase10.py` (32 tests),
  `tests/integration/test_model_lifecycle_api.py` (7 tests)

### Affected Components
- Missing: deterministic training dataset builder, TrainingRun lineage, candidate
  staging paths, validation gates, Champion/Challenger comparison, lifecycle
  status on the model registry, training worker isolation.

### Root Cause
The repository had production-grade training infrastructure
(`WalkForwardTrainer`, `ScalpNet`, `experience_model_registry`, schema
registry) but NO controlled-training boundary: nothing prevented a training
run from overwriting the Champion artifact, nothing recorded immutable
TrainingRuns, and there was no candidate/Challenger lifecycle or validation
gate chain. A retrain was effectively an uncontrolled mutation of the
production model path.

### Fix
Built `src/nexus_scalp/model_lifecycle/`:
- deterministic causal TrainingDatasetBuilder over the experience ledger,
- ChallengerTrainer writing only to `candidate/<run_id>/` staging paths —
  the Champion artifact is never overwritten (tested via hash invariance),
- 12 validation gates + collapse guard,
- Champion vs Challenger multi-dimension comparator,
- additive lifecycle status columns on the existing `experience_model_registry`
  (no duplicate registry),
- immutable `training_runs` + `model_comparisons` tables,
- isolated/bounded/cancellable TrainingWorker wired into LiveEngine via
  `asyncio.to_thread` (never in the tick pipeline).

### Regression Guards
- Champion artifact hash must stay unchanged across a training run (tested).
- Failed/interrupted training remains FAILED/INCOMPLETE, never VALIDATED.
- No auto-promotion: validated Challenger stays shadow-eligible (tested).
- Schema mismatch (dimension/class/scaler) fails explicitly (tested).

---

## BUG-025 — Shadow Decision INSERT 31 Values for 30 Columns (Silent Total Data Loss)

- **Status**: FIXED
- **Severity**: CRITICAL
- **Confidence**: HIGH
- **Discovered**: Phase 11 Forensic Audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_shadow_phase11.py` (35/35 green; previously hung)

### Affected Components
- `src/nexus_scalp/shadow/store.py` (`_INSERT_DECISION_SQL`)

### Problem
Every shadow-decision insert failed with `error=31 values for 30 columns`:
the SQL column list had 30 columns but the VALUES clause had 31 `?`
placeholders. The background queue worker logged the error and **dropped the
row**. No shadow decision was ever persisted in production.

### Root Cause
The VALUES placeholder count drifted from the column list when the
`hypothetical_pnl_usd` field was added; the mismatch was never caught because
`test_shadow_outcomes_persisted` **hung** on `queue.join()` (see BUG-026) and
was therefore never observed as a failure.

### Evidence
- Column list: 30 names (`shadow_decision_id` .. `payload`).
- VALUES clause: 31 placeholders. Verified by regex count + live repro:
  `Audit Background Worker failed to insert batch error=31 values for 30 columns`.

### Failure Scenario
Attaching a Challenger and running shadow evaluation produced zero persisted
decisions; the comparison and promotion layers had no data to evaluate.

### Impact
Phase 11 shadow evaluation was completely non-functional at the persistence
layer (silent data loss).

### Fix
Removed the extra placeholder so 30 values map to 30 columns.

### Regression Tests
- `test_shadow_outcomes_persisted` now passes (row round-trips through the DB).

### Verification
`pytest tests/unit/test_shadow_phase11.py` → 35 passed.

### Architectural Lessons / Regression Guards
- Any INSERT with N columns must have exactly N placeholders; add a
  static placeholder-count smoke test when schema columns change.

---

## BUG-026 — Audit Queue Worker Never Calls task_done() on Insert Error (Permanent Deadlock)

- **Status**: FIXED
- **Severity**: CRITICAL
- **Confidence**: HIGH
- **Discovered**: Phase 11 Forensic Audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_shadow_phase11.py::TestShadow::test_shadow_outcomes_persisted` (no longer hangs)

### Affected Components
- `src/nexus_scalp/adapters/database/audit_repository.py` (`_process_queue_worker`)

### Problem
When a batch insert failed (e.g. BUG-025's 31-vs-30 placeholder mismatch), the
`except` branch logged and slept but **never called `task_done()`** for the
already-`get()`-ed items. Any subsequent `queue.join()` — including
`AuditRepository.close()` and test teardown — blocked forever. The failed
items were also lost (never re-queued, never persisted).

### Root Cause
The error path of the bulk-transaction loop omitted the bookkeeping that the
success path performed.

### Evidence
`test_shadow_outcomes_persisted` hung indefinitely at `repo._queue.join()` with
the worker logging `error=31 values for 30 columns` in a loop.

### Failure Scenario
Any persistent insert error (bad SQL, schema drift, DB lock) permanently
deadlocked every `join()` caller: engine shutdown (`close()`), worker teardown,
and test fixtures.

### Impact
Unrecoverable hang on shutdown; silent loss of the failed rows.

### Fix
The error path now also calls `task_done()` for each item in the failed batch
before backing off. `join()` can always return; failed rows are logged as lost
instead of wedging the process.

### Regression Tests
- `test_shadow_outcomes_persisted` completes instead of hanging.

### Verification
`pytest tests/unit/test_shadow_phase11.py` → 35 passed.

### Architectural Lessons / Regression Guards
- `queue.get()` must ALWAYS be paired with `task_done()` in every path
  (success AND failure). Add a failure-injection test for the queue worker.

---

## BUG-027 — Shadow Comparison Numerically Degenerate (Champion R Proxied as Challenger R)

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Phase 11 Forensic Audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_shadow_phase11.py::TestRegimeStrategy::test_critical_regime_degradation_not_averaged_away`

### Affected Components
- `src/nexus_scalp/shadow/comparison.py` (`ShadowComparer.compare`,
  `evaluate_promotion`)

### Problem
The comparison used `champ_r = [d.hypothetical_r * 1.0 ...]` — the champion's
realized R was numerically identical to the challenger's. Per-regime and
per-strategy deltas were therefore always `0.0`, and the
`degraded_regimes` / `degraded_strategies` / `improved_strategies` /
promotion-veto signals could never fire. Shadow-based promotion evaluation was
statistically meaningless.

### Root Cause
`hypothetical_r` is the challenger's realized R on the simulated path. When the
two models disagree on direction, the champion's R on the SAME path has the
opposite sign; the code ignored this and reused the challenger's value.

### Evidence
`test_critical_regime_degradation_not_averaged_away` failed: HIGH_VOLATILITY
delta stayed `0.0` and `degraded_regimes=[]` despite -0.9R challenger outcomes.

### Failure Scenario
A Challenger that collapses in a critical regime shows no degradation signal;
promotion vetoes never trigger; a genuinely worse Challenger could look neutral.

### Impact
False confidence in Champion/Challenger comparisons; broken promotion
eligibility signals.

### Fix
- Champion-side R is now derived from the champion's OWN action on the same
  path: identical to `hypothetical_r` when actions agree, `-hypothetical_r`
  when they disagree (one wins, one loses).
- Per-regime/per-strategy/per-session aggregation uses the same derived
  champion-side R.
- Regimes are additionally flagged degraded when the challenger's absolute
  expectancy falls below `MIN_REGIME_EXPECTANCY_R` (0.0), so a bad regime is
  never averaged away by good ones.

### Regression Tests
- `test_critical_regime_degradation_not_averaged_away` (unchanged) now passes.

### Verification
`pytest tests/unit/test_shadow_phase11.py` → 35 passed.

### Architectural Lessons / Regression Guards
- Any "champion vs challenger" numeric comparison must derive each side from
  its OWN action/decision, never proxy one from the other.
- Absolute degradation floors complement relative deltas so critical regimes
  cannot hide behind good ones.

---

## BUG-028 — ShadowStore/ModelLifecycleStore None-Repo Guard Missing (Isolation Contract)

- **Status**: FIXED
- **Severity**: MEDIUM
- **Confidence**: HIGH
- **Discovered**: Phase 11 Forensic Audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `tests/unit/test_shadow_phase11.py::TestFailureIsolation` (5/5)

### Affected Components
- `src/nexus_scalp/shadow/store.py` (all save/list methods)
- `src/nexus_scalp/model_lifecycle/store.py` (all save/list methods)

### Problem
A store constructed with `audit_repo=None` raised `AttributeError:
'NoneType' object has no attribute '_is_sqlite'` instead of failing closed /
isolating. The failure-isolation contract (a broken store must never raise
through the engine) was violated.

### Fix
All guards now use `if not self.audit_repo or not self.audit_repo._is_sqlite:`.

### Regression Tests
- `test_shadow_db_failure_cannot_stop_trading` (unchanged) now passes.

### Verification
`pytest tests/unit/test_shadow_phase11.py::TestFailureIsolation` → 5 passed.

### Architectural Lessons / Regression Guards
- Every persistence guard must be None-safe; a degraded store degrades to
  "return False / no-op", never to an exception.

---

## BUG-029 — Shadow Store ensure_schema() Synchronous SQLite I/O on Live Tick Path

- **Status**: FIXED
- **Severity**: MEDIUM
- **Confidence**: HIGH
- **Discovered**: Phase 11 Forensic Audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `pytest tests/unit/test_shadow_phase11.py` (35/35) + micro-benchmark

### Affected Components
- `src/nexus_scalp/shadow/store.py` (`ShadowStore.ensure_schema`)
- `src/nexus_scalp/application/live_engine.py` (`_record_shadow_decision`)

### Problem
`ShadowStore.save_decision()` -> `ensure_schema()` opened a synchronous
`sqlite3.connect()` + 4 `CREATE TABLE IF NOT EXISTS` + 3 `CREATE INDEX` +
`commit()` on EVERY live tick while a Challenger was attached (~0.65ms per
cycle benchmarked; ~13ms/s at 20 ticks/s). This is blocking DB I/O on the hot
path, violating the "NO Phase 08-11 work may block the live tick path"
invariant. A per-tick `[SHADOW] event=DECISION` info log also spammed output.

### Fix
`ensure_schema()` is now guarded by an in-process `_schema_ensured` flag: the
DDL runs at most once per process; every subsequent call is a no-op returning
in microseconds. The per-tick decision log was left intact (bounded by active
shadow runs) but no longer has DDL cost behind it.

### Regression Tests
- Full shadow suite still green (persistence round-trip unchanged).

### Verification
Micro-benchmark: per-call cost drops from ~0.65ms to ~0.0002ms after the first
call.

### Architectural Lessons / Regression Guards
- Schema DDL belongs in explicit init paths, never behind a per-record write.
- Any `ensure_schema()` invoked from write paths must be process-guarded.

---

## BUG-030 — Phase Tables Use INSERT OR REPLACE on "Immutable" Rows (Id-Churn, Not Data Loss)

- **Status**: WONT_FIX (documented; low risk given UUID keys)
- **Severity**: LOW
- **Confidence**: HIGH
- **Discovered**: Phase 08-11 Forensic Audit / DB Schema Audit (2026-08-16)

### Affected Components
- `src/nexus_scalp/shadow/store.py` (`_INSERT_RUN_SQL`, `_INSERT_DECISION_SQL`,
  `_INSERT_COMPARISON_SQL`, `_INSERT_PROMOTION_SQL`)
- `src/nexus_scalp/model_lifecycle/store.py` (`_INSERT_RUN_SQL`,
  `_INSERT_COMPARISON_SQL`)
- `src/nexus_scalp/intelligence/evolution.py` (`strategy_evolution_candidates`)

### Problem
Six phase tables documented as "append-only / immutable" use
`INSERT OR REPLACE` on UNIQUE keys. REPLACE = DELETE+INSERT, which rewrites
the AUTOINCREMENT row id and can in principle orphan cross-table references
(e.g. `model_comparisons.run_id`), contradicting the immutability claim.
The correct append-only pattern used elsewhere is `INSERT ... ON CONFLICT
DO NOTHING` (experiences, lifecycle events).

### Impact
Low in practice: all writes carry freshly generated UUID keys
(`shadow_<hex>`, `run_<hex>`, `sd_<hex>`), so REPLACE virtually never
collides; row-id churn is invisible to consumers who key on the UUID.
No data loss occurs.

### Recommendation (future)
Switch these to `ON CONFLICT(run_id/shadow_decision_id) DO NOTHING` when
next touching these files. Not a production-safety defect today.

---

## BUG-031 — Phase ensure_schema() Has No ALTER-Based Migration Path

- **Status**: WONT_FIX (documented; future-schema readiness item)
- **Severity**: MEDIUM
- **Confidence**: HIGH
- **Discovered**: Phase 08-11 Forensic Audit / DB Schema Audit (2026-08-16)

### Affected Components
- `src/nexus_scalp/shadow/store.py::ensure_schema`
- `src/nexus_scalp/model_lifecycle/store.py::ensure_schema`
- research/intelligence phase stores

### Problem
Phase stores create tables with `CREATE TABLE IF NOT EXISTS` + indexes only.
If a phase table already exists with an older column set, a later release
adding columns silently does nothing — the INSERT then fails with
`no such column` at write time, and the queued worker drops the whole batch
(BUG-026 path). audit_repository.py handles this with defensive
`ALTER TABLE ... ADD COLUMN` in try/except; phase stores do not.

### Impact
Future schema evolution of phase tables (e.g. 60D/350D feature schemas)
requires a manual migration step or a hard DROP + recreate (data loss).
Not a defect in the current 50D schema.

### Recommendation (future)
Centralize schema migration: a shared `_add_columns_if_missing(conn, table,
cols)` helper used by every phase store before writes, or a schema_version
table with explicit migrations.

---

## BUG-032 — Queue-Full Drops Telemetry Silently; audit_signals Dedup Is In-Memory Only

- **Status**: WONT_FIX (bounded-by-design; documented)
- **Severity**: LOW
- **Confidence**: HIGH
- **Discovered**: Phase 08-11 Forensic Audit / DB Schema Audit (2026-08-16)

### Affected Components
- `src/nexus_scalp/adapters/database/audit_repository.py` (`log_signal`,
  `_process_queue_worker`)

### Problem
1. `_queue.put_nowait` on a full queue (maxsize 10000) drops the record with
   only a log line — no spill, no retry. Bounded and intentional (hot-path
   protection), but a 10k-row burst loses telemetry.
2. `audit_signals` dedup is a single in-memory `_last_logged_signal_key`
   (protects consecutive duplicates only; lost on restart) — no UNIQUE
   constraint on the table, so restart/worker-crash can produce duplicate
   signal rows.

### Impact
Observability loss under extreme bursts; duplicate signal rows after crash.
Neither affects trading decisions (audit is telemetry).

### Recommendation (future)
Add a SQL-level dedup (e.g. UNIQUE index on the 5-tuple) or accept the
bounded-loss contract and document it in skill.md (already documented as
"queue full -> drop telemetry").

---

## BUG-033 — CurrentNewsContext Rebuilt Inside Tick Path on TTL Expiry (Per-60s Sync DB Read on Event Loop)

- **Status**: FIXED (2026-08-16, Phase 12 completion)
- **Severity**: MEDIUM
- **Confidence**: HIGH
- **Discovered**: Phase 12 forensic audit (live code inspection)

### Symptom
`CurrentNewsContextCache.get()` rebuilt the context (a synchronous SQLite
`SELECT * FROM news_analysis ORDER BY analyzed_at DESC LIMIT 100`) whenever
the 60s TTL expired — including when called from
`LiveEngine._process_tick_pipeline()` via `news_engine.current_context()`.
Violates the "no DB access on the live tick path" invariant.

### Evidence
`src/nexus_scalp/news/context.py` — `get()` had `if (now_mono - last)<ttl: return cached; self._context = self.build()` (build → `db.list_analysis`).
`live_engine.py` `_process_tick_pipeline` called `current_context()` per tick.

### Root Cause
The tick path triggered the TTL-expired rebuild itself instead of relying on
the background worker to refresh the context off-loop.

### Fix
- `NewsContextCache.get()` is now **cache-only** on the live path (returns
  the cached object or a safe first-run default; NEVER touches the DB).
- New `NewsContextCache.refresh()` rebuilds from DB and is called by the
  NewsWorker cycle (`asyncio.to_thread`) and by `engine.self_heal()` /
  explicit API requests (`force=True`) only.
- `NewsEngine.current_context(force=True)` remains for API/worker/self-heal.

### Verification
- Unit: `test_53_context_cache_bounded` passes (cache returns same timestamp).
- Full regression: 406 unit + 56 integration tests green.
- Live path now reads only an in-memory object per tick.

---

## BUG-034 — Seeded Source URLs Broken (BEA 404 / CFTC RSS 404 / Treasury RSS 503) — Silent Empty Feeds

- **Status**: FIXED (2026-08-16, Phase 12 completion)
- **Severity**: MEDIUM
- **Confidence**: HIGH (verified by live HTTP checks)
- **Discovered**: Phase 12 source-reachability audit

### Symptom
Several Tier-1 official source URLs were dead, causing the fetcher to
silently fail every cycle for those sources (no articles, no error surfaced).

### Evidence (2026-08-16 live checks)
- BEA `https://www.bea.gov/rss/news` → HTTP 404
- CFTC `https://www.cftc.gov/RSS/CFTC_RSS.xml` → HTTP 404 (no public CFTC
  RSS exists; RSS/rss.aspx also 404)
- U.S. Treasury `https://home.treasury.gov/rss/press-releases.xml` → HTTP 503
- Working alternatives verified: `https://www.bea.gov/news` (200),
  `https://home.treasury.gov/news/press-releases` (200)

### Fix
- `seed.py` (SEED_VERSION bumped to `2026-08-16-v2`): BEA and Treasury now
  point at the verified live pages (HTML extraction path); CFTC registered
  but **disabled by default** (`enabled: False`) since no public feed exists.
- The fetcher health tracker continues to mark unreachable sources
  unhealthy/backed-off after consecutive failures instead of silent empty.

### Verification
- Seed idempotency test (`test_50_seed_idempotent`) passes.
- `test_07_source_disablement` passes.
- Fresh DB: 10 enabled sources, CFTC disabled; unit suite green (406).

---

## BUG-033 — Packaged EXE Ran Legacy Argparse Launcher Instead of the Release CLI

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Release Engineering build (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: packaged EXE `version --plain` / `health --json` return the Typer CLI output

### Affected Components
- `scripts/build/build_release.ps1` / CI `release.yml` (PyInstaller entrypoint)
- `src/nexus_scalp/release/packaged_main.py` (new)

### Problem
The first PyInstaller onedir build used `NexusTradingForexBot.py` as the
entrypoint. PyInstaller packages that script as `__main__`, so the EXE exposed
the argparse launcher (`--config/--doctor/--gateway/--symbol`) and rejected
`version --plain` / `health --json` with "unrecognized arguments". The
packaged product had no release CLI surface at all.

### Root Cause
The launcher entrypoint predates the release CLI; the release build reused it
without checking what CLI surface it exposes.

### Fix
New `src/nexus_scalp/release/packaged_main.py` — a PyInstaller entrypoint that
delegates to the Typer `nexus` app. Both build paths (local ps1 + CI) now
build from it.

### Regression Guards
- `tests/unit/test_release_build_system.py::test_build_scripts_reference_packaged_entrypoint`
- packaged EXE smoke (build_release.ps1 + verify_release.ps1) asserts
  `version --plain` and `health --json` succeed.

---

## BUG-034 — Silent Uninstall Aborted Because the Data-Preservation Wizard Page Required Input

- **Status**: FIXED
- **Severity**: MEDIUM
- **Confidence**: HIGH
- **Discovered**: Installer smoke test (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `clean_install_test.ps1` silent install → uninstall passes (exit 0)

### Affected Components
- `installer/NexusScalpEngine.iss` (custom uninstall wizard page)

### Problem
`unins000.exe /VERYSILENT` exited 1: the custom `CreateInputOptionPage`
("preserve your data?") required input pages in non-interactive uninstall.
Automated/CI uninstalls therefore failed and could leave the app installed.

### Root Cause
The uninstall wizard page was shown unconditionally; silent mode has no way to
answer it.

### Fix
Deletion of user data now happens only when `not UninstallSilent` AND the
checkbox is ticked. Silent uninstall always preserves user data (exit 0).

### Regression Guards
- Installer smoke: silent install → reinstall → uninstall all exit 0.
- User data under `{localappdata}\NexusScalpEngine` preserved after
  uninstall (checked by `clean_install_test.ps1`).

---

## BUG-035 — Runtime Web/News Dependencies Missing from Canonical Dependency Declarations

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Release Engineering dependency audit (2026-08-16)
- **Fixed**: 2026-08-16
- **Verified**: `pip install -e .[web,release]` + packaged EXE launches web server deps

### Affected Components
- `pyproject.toml` `[project] dependencies` + `[project.optional-dependencies]`
- `requirements.txt`

### Problem
`fastapi`, `uvicorn`, `httpx` were required at runtime (web server, news
ingestion) but declared nowhere in `pyproject.toml` dependencies —
`ci.yml` papered over this with a manual `pip install fastapi uvicorn httpx`.
`feedparser` (used by the Phase 12 news sources) was also undeclared. Any
clean install or packaged build without the manual pip step silently lacked
the web/news runtime.

### Root Cause
Dependency declarations drifted from the runtime import graph (the `web`
extra was referenced by ci.yml but never defined).

### Fix
- Added `fastapi`, `uvicorn`, `httpx` (+ `feedparser` conditional) to core deps,
  defined `web` and `release` extras (pyinstaller), and mirrored the runtime
  list in `requirements.txt`.
- `ci.yml` now installs `.[dev,web]` with no manual fallback.

### Regression Guards
- `tests/unit/test_release_build_system.py::test_requirements_cover_web_and_news_runtime`
- fresh venv `pip install -e .` pulls the full runtime.

---

## BUG-036 — News-Aware Candidate Manifests Did Not Record the Neural Input Width (Load/Replay Mismatch)

- **Status**: FIXED (2026-08-16, Phase 13 migration)
- **Severity**: HIGH (news-aware models could not be reloaded)
- **Confidence**: HIGH (proven by integration test)
- **Discovered**: Phase 13 model-generation migration (runtime load path)

### Symptom
A candidate trained with `news_enabled=true` (50 base features + 12 news
dims = 62 inputs) could not be loaded by `LocalModelRuntime`: the runtime
reconstructed the model with `input_dim = feature_dimension (50)`, so the
state_dict load failed with a shape mismatch. `SampleReplay` also predicted
with the wrong width.

### Evidence
`training.py` wrote `feature_dimension=len(feat_cols)` (50) while the model
was built with `input_dim = len(feat_cols)+len(news_cols)` (62). The runtime
had no record of the actual neural input width.

### Root Cause
The manifest stored only the BASE feature schema dimension; the extra news
dimensions were implicit in the state dict shape but not recorded.

### Fix
- `training.py`: `build_metadata["input_dimension"]` records the exact
  neural input width (base + news).
- `runtime.py`: model construction + `predict()` input validation use
  `input_dimension`; a manifest whose `input_dimension < feature_dimension`
  or (news disabled yet dims differ) is REJECTED as corrupted.
- `replay.py`: when the model is news-aware, the replay appends the sample's
  news context vector (schema order) before predicting.

### Verification
- `tests/integration/test_model_generation.py::test_full_artifact_flow` —
  news-aware candidate loads + predicts with DB import blocked.
- `tests/unit/test_model_generation_phase13.py::test_36/37/38` — corrupted
  manifests and narrowed schemas now raise `ManifestValidationError`.
- All 55 Phase 13 tests pass.

---

## BUG-037 — Docstring/Manifest Claimed 3-Class Contract While Legacy Head Outputs 4 (Contract Ambiguity)

- **Status**: DOCUMENTED (by design — legacy bridge, not a label)
- **Severity**: LOW
- **Confidence**: HIGH
- **Discovered**: Phase 13 migration (label contract audit)

### Symptom
The labeler is 3-class (NO_TRADE/BUY/SELL; WAIT is policy-derived) but the
legacy ScalpNet head outputs 4 logits (0=NO_TRADE,1=BUY,2=SELL,3=WAIT).
Without an explicit contract this looks like a bug.

### Root Cause
Legacy architecture: the 4th logit is a POLICY bridge (WAIT state), never a
training label. Phase 10's `EXPECTED_NUM_CLASSES=4` encoded this implicitly.

### Resolution
Phase 13 makes the contract explicit:
- `LabelSchema` (`triple_barrier_3class_v1`): class_count=3, WAIT is NOT a
  label — `schema.encode("WAIT")` raises.
- `ModelManifest.class_count=3` + `classes=[NO_TRADE,BUY_MARKET,SELL_MARKET]`;
  `ModelFactory` keeps the legacy 4-head geometry ONLY for
  `LEGACY_SCALPNET_V1` (with an explicit comment) and 3 heads for all new
  architectures.
- Tests enforce: label layer rejects class 3; runtime decode maps argmax 3 →
  policy WAIT in the legacy baseline only.

### Verification
- `test_09_label_mismatch_rejected`, `test_22_3class_label_contract_enforced`.

---

## BUG-038 — Artifact Store ID Path Traversal (model_id/dataset_id Unsanitized)

- **Status**: FIXED (2026-08-16, Phase 13 forensic supervision audit)
- **Severity**: MEDIUM (security; no exploit in production since ids are
  generated internally, but the public API was unsafe)
- **Confidence**: HIGH
- **Discovered**: Forensic audit T03/T58 (path traversal review)

### Symptom
`ArtifactStore.model_dir(model_id)` / `dataset_dir(dataset_id)` /
`experiment_path(experiment_id)` concatenated the raw id into a `Path`
without validation. A model_id like `../champion` or `../../etc` would
resolve OUTSIDE the artifact root.

### Evidence
`artifact_store.py` — `return self.models_dir / model_id` with no
sanitization; id values come from callers (CLI `--model`, tests, API).

### Root Cause
No identifier validation at the store boundary; ids assumed trusted.

### Fix
`validate_artifact_id()`: only `[A-Za-z0-9_.-]`, no `..`, no path
separators; applied to model/dataset/experiment path builders. Invalid ids
raise `ValueError`.

### Verification
`test_board_path_traversal_rejected` + `test_board_store_refuses_traversal_through_api`
— traversal ids raise; safe ids accepted.

---

## BUG-039 — CandidateTrainer Trained Raw Features, Never Persisted a Scaler (Distribution Parity Gap)

- **Status**: FIXED (2026-08-16, Phase 13 forensic supervision audit)
- **Severity**: HIGH (training↔inference distribution mismatch)
- **Confidence**: HIGH
- **Discovered**: Forensic audit T24 (scaler/preprocessor)

### Symptom
`CandidateTrainer.train_candidate` trained on UN-normalized raw features
and saved artifacts with `scaler=None`. The manifest declared `scaler_hash`
as `""` while the legacy WalkForwardTrainer (and the production champion
path) always fits + persists a scaler (mean/std). A model-generation
candidate therefore had no reproducible distribution transform; the runtime
silently skipped scaling.

### Evidence
`training.py` line ~194 `scaler=None`; `runtime.py` scaling block gated on
`self._scaler is not None` (silent skip).

### Root Cause
The artifact-first pipeline had not wired the train-fitted scaler that the
rest of the system treats as an invariant.

### Fix
- `CandidateTrainer`: fits mean/std on the TRAIN split ONLY (zero leakage
  into val/OOS), trains + evaluates on scaled features, persists the scaler
  with the artifact (fixing an `np.savez` path bug found in the same sweep,
  BUG-040).
- `LocalModelRuntime.load`: if the manifest DECLARES a scaler hash but the
  scaler file is missing/corrupt, loading FAILS (no silent unscaled
  prediction).

### Verification
`test_board_scaler_persisted_and_roundtrips` (scaler file + hash +
deterministic scaled prediction), `test_board_missing_declared_scaler_blocks_load`
(missing scaler with declared hash -> ManifestValidationError).

---

## BUG-040 — np.savez Appends ".npz" Breaking Atomic Scaler Replace

- **Status**: FIXED (2026-08-16, Phase 13 forensic supervision audit)
- **Severity**: MEDIUM (crashed every scaler save once scaler wiring landed)
- **Confidence**: HIGH
- **Discovered**: Forensic audit T06/T24 (exercised the new scaler path)

### Symptom
`np.savez(tmp_path, ...)` appends `.npz` to its path argument; the code then
attempted `tmp_path.replace(final_path)` on a file that did not exist
(`scaler.npz.tmp`), raising `FileNotFoundError` (WinError 2) on every model
save that included a scaler.

### Evidence
`artifact_store.py` `save_model_artifact` scaler branch:
`np.savez(tmp_s, ...)` then `tmp_s.replace(scaler_path)` — the actual file
written was `tmp_s + ".npz"`.

### Root Cause
`np.savez`'s implicit `.npz` suffix was not accounted for.

### Fix
Save to `scaler.tmp` (no suffix), then atomically rename
`scaler.tmp.npz` -> `scaler.npz`, with `finally` cleanup of both leftovers.

### Verification
All Phase 13 training tests pass with the scaler persisted
(`test_board_scaler_persisted_and_roundtrips`), no `.tmp`/`tmp.npz`
leftovers (audit T03 concurrency/atomicity check).

---

## BUG-041 — NaN/Inf Training Inputs Produced COMPLETED (Garbage) Candidates Instead of FAILED

- **Status**: FIXED (2026-08-16, Phase 13 forensic supervision audit, round 2)
- **Severity**: HIGH (a NaN-trained model could reach CHALLENGER eligibility)
- **Confidence**: HIGH (proven by adversarial probe)
- **Discovered**: Forensic audit T29 (failed-training simulation)

### Symptom
A dataset containing NaN/Inf feature values trained successfully: `status="COMPLETED"` with `val_acc=0.0000`. NaN loss propagates silently through the optimizer → a numerically garbage model that looks trained. Violates the invariant "failed training is FAILED, never CHALLENGER".

### Evidence
Adversarial probe with `feat_0=[nan,1,2]` + `MLP_V2` produced
`[TRAIN] event=CANDIDATE_READY val_acc=0.0000`. The training path never
validated input finiteness.

### Root Cause
No finite-input gate before the training loop; NaN/Inf features flow
straight into loss.backward().

### Fix
`CandidateTrainer.train_candidate` now rejects non-finite feature matrices
up front: `if not np.isfinite(X_arr).all(): return {"status": "FAILED", ...}`.
(The 3-class label schema already rejects invalid label values.)

### Verification
`test_board_nan_features_fail_training`, `test_board_inf_features_fail_training`,
`test_board_nan_labels_fail_training` — all assert FAILED + reason.

---

## BUG-037 — Onefile Packaged CLI Exited 1 on `--help` (sys.exit Wrapped Typer SystemExit)

- **Status**: FIXED
- **Severity**: MEDIUM
- **Confidence**: HIGH
- **Discovered**: Runtime test pass (2026-08-16 hardening)
- **Fixed**: 2026-08-16
- **Verified**: `tests/runtime/test_packaged_cli.ps1` — `--help` now exits 0

### Affected Components
- `src/nexus_scalp/release/cli_shim.py`

### Problem
The onefile `NexusScalpEngine-CLI.exe --help` exited 1 instead of 0. The
source interpreter path (`python cli_shim.py --help`) exited 0, so the defect
only appeared in the frozen PyInstaller build — the exact class of packaging
bug the runtime test suite exists to catch.

### Root Cause
The app-level Typer help string contained a U+2014 EM DASH
("Nexus Trading Forex Bot — operational [and] release console"). The frozen
onefile console encodes output in the active code page; the em dash maps to
`<undefined>` and the script aborted with `unhandled exception` + a code-page
error, exiting 1. The `sys.exit(app())` wrap was a contributing factor but not
the primary defect.

### Fix
- Replaced non-ASCII characters (em dash, arrow) in every Typer `help=`
  string with ASCII-safe equivalents (`-`, `to`).
- `cli_shim.py` now calls `app()` directly and lets Typer's own `SystemExit`
  propagate (kept as defence-in-depth; documented in the module docstring).

### Regression Guards
- `tests/runtime/test_packaged_cli.ps1` (`--help` exits 0).
- `test_cli_version_and_help` asserts help/version exit 0.

---

## NOTE — Phase 13B Benchmark-Era Pre-Merge Defects (fixed in new code, no BUG id)

The TCN_ATTENTION_V1 benchmark introduced new modules; two defects were
found and fixed BEFORE merge (no production impact, no persisted artifact
became invalid):

1. **SequenceBuilder news leak with news_enabled=False** — the sequence
   feature vector always appended `news_*` columns, so a news-OFF TCN
   reported `input_dimension=62` instead of 50, violating the runtime's
   manifest consistency guard (failed at load). Fixed by threading
   `news_enabled` through `SequenceBuilder` / `SequenceCandidateTrainer` /
   benchmark prediction helper. Regression: `test_11_news_off_input_50`,
   `test_12_news_on_input_62`.

2. **Lexicographic feature ordering** — `sorted(feat_*)` reordered
   `feat_10` before `feat_2`, silently diverging from the frame-order used
   by the 2D trainer and DatasetFactory. Fixed to frame-order everywhere
   (no index shifts between training/eval paths). Regression:
   `test_05_causal_no_future` (vector == frame row values).

3. **Benchmark `_predict_probs` 2D path ignored the manifest's
   news_enabled** (scaler 50-wide vs 62-wide X broadcast error). Fixed to
   read the manifest. Regression: benchmark matrix runs green.

Also note: agents/bugs.md contains a pre-existing id collision — the
release-work branch reused BUG-037 after BUG-041 (a parallel-stream WIP
item). No action taken (out of audit scope); future entries should
continue from BUG-044+ to avoid overlap.

## BUG-038 — Packaged `nexus repair` Could Not Find Config Template (PyInstaller _internal Layout)

- **Status**: FIXED
- **Severity**: MEDIUM
- **Confidence**: HIGH
- **Discovered**: Runtime repair test (2026-08-16 hardening)
- **Fixed**: 2026-08-16
- **Verified**: `tests/runtime/test_repair.ps1` — repair restores config from template

### Affected Components
- `src/nexus_scalp/release/repair.py` (`RepairEngine._default_template`)

### Problem
In the packaged onedir layout, `configs/base.yaml` lands under
`_internal/configs/` (PyInstaller data dir). `RepairEngine` looked only at
`<workspace>/configs/base.yaml`, so `nexus repair --recreate-config` reported
`SKIPPED: no template found` and never restored a deleted user config —
exactly the case the repair command exists for.

### Root Cause
The template lookup assumed the source checkout layout; the packaged layout
(`_internal/`) was not considered.

### Fix
`_default_template()` now also checks `<workspace>/_internal/configs/base.yaml`
(and the `live.yaml.example` fallback in both locations).

### Regression Guards
- `tests/runtime/test_repair.ps1` restores config on the real packaged EXE.
- Synthetic fixture in `tests/unit/test_release_system.py` still covers the
  source-layout path.

---

## BUG-039 — `--help` Non-ASCII in Typer help strings Broke Frozen CLI (documented root cause)

- **Status**: FIXED (see BUG-037 for the full story)
- **Severity**: MEDIUM
- **Discovered**: Runtime CLI test (2026-08-16 hardening)
- **Verified**: `tests/runtime/test_packaged_cli.ps1` (`--help` exit 0);
  ASCII-only regression test in `tests/release/test_build_script_hardening.py`
- **Guard**: `test_cli_help_strings_are_ascii_safe` fails if any `help=`
  string is non-ASCII."""

## BUG-040 — Web API Exposed Raw Exception Text / Stack Traces to Clients (CodeQL py/stack-trace-exposure)

- **Status**: VERIFIED
- **Severity**: HIGH
- **Confidence**: HIGH
- **Discovered**: Dashboard hardening audit (2026-08-17)
- **Fixed**: 2026-08-17
- **Verified**: `tests/unit/test_web_security.py` (behavioral payload/log assertions)

### Affected Components
- `src/nexus_scalp/web/server.py` (all FastAPI routes, SSE, WebSocket, news/accounting/research/model/shadow endpoints)

### Problem
Repeated `except Exception as e: return {... "error": str(e)}` patterns returned
raw exception text (including filesystem paths, SQL fragments, exception class
names) to API clients. CodeQL flagged these as `py/stack-trace-exposure`
(information leakage) across the API surface.

### Root Cause
No centralized error contract; each route implemented its own inline handler with
`str(e)` in the public payload. The SSE generator and WebSocket handler also had
no sanitized error path.

### Fix
- New `src/nexus_scalp/web/errors.py`: request-correlation IDs, `safe_error_payload`
  (stable code + generic message + request_id), `log_web_error` (full traceback to
  logs only), and an HTTP middleware that sanitizes unhandled 500s and echoes
  `X-Request-ID`.
- `server.py`: every leaking return converted to `_err(code)`; every logger.error
  with `error=str(e)` converted to `_log_err(exc, msg)`; model-test/health/sse/ws
  paths sanitized (no f-string exception interpolation anywhere).
- Frontend: new `Web/api_client.js` central API client (X-Request-ID header,
  safe error parsing, [UI_ERROR] diagnostics, deduped GETs) wired before app.js;
  SSE hardened with bounded exponential reconnect + stale detection.

### Regression Guards
- `tests/unit/test_web_security.py` asserts payload has no traceback/path/SQL,
  has stable error code, has request_id (header + body), and server log (via
  structlog) contains the detailed exception.

### Verification
`pytest tests/unit/test_web_security.py tests/integration/test_accounting_api.py
tests/integration/test_intelligence_api.py tests/integration/test_research_api.py
tests/integration/test_model_lifecycle_api.py tests/integration/test_news_api.py`
all pass. `str(e)` count in server.py returns: 0.
---

## BUG-042 — Benchmark Used Synthetic 10-Row News Fixture, Not Real News (Phase 13B Forensic)

- **Status**: FIXED (bridge + readiness gate implemented; real benchmark blocked until gate GREEN)
- **Severity**: HIGH
- **Confidence**: HIGH (proven by artifact inspection 2026-08-17)
- **Discovered**: Phase 13B News Forensic Audit
- **Verified**: `tests/unit/test_news_bridge_phase13b.py`, `tests/unit/test_news_bridge_contract_phase13b.py`

### Problem
The 2026-08-16 A/B/C/D benchmark (`model_benchmark_report.json`, dataset
`ds_cb30f87520e9e6a4`) was driven by a **synthetic 10-row news fixture**
(`rows_news=10`): 770/800 dataset samples were repetitions of only **4
synthetic events**; **7 of 12 NewsContext fields were permanently zero**
(active_high_impact_events, conflict_score, novelty, freshness,
source_consensus, news_state, time_since_event_sec), and the nonzero fields
were highly redundant (xau~usd 0.925, xau~bearish 0.953, xau~confidence
0.913). Result: `NEWS_INCONCLUSIVE` was an artifact of NO REAL NEWS DATA,
not of news being useless.

### Root Cause
1. `BenchmarkRunner` accepted a caller-supplied `news_frame`; nothing in the
   repository ever exported the News subsystem DB into that shape.
2. `artifacts/news.db` does not exist — no real collection ever ran.
3. The legacy `SampleFactory.news_context_at` copied a single prior row
   verbatim into all 12 fields (leaving 7 dead) and could mis-select with
   duplicate timestamps.

### Fix
- New `src/nexus_scalp/model_generation/news_bridge.py`: causal 12-field
  bridge (`normalize_news_frame`, `news_context_at`, `build_news_frame_from_db`),
  categorical encoding (news_state 0-5, novelty 0-4), NaN/Inf sanitization,
  Windows-safe epoch extraction, quality diagnostics + readiness gate.
- `SampleFactory.news_context_at` delegates to the bridge.
- `DatasetFactory.build` records news provenance (`news_version`,
  `news_data_range`, content digest) in the manifest and folds news content
  into the deterministic dataset id (news changes ⇒ new dataset).
- CLI `model-dataset-build --with-news --news-db <path>` exports the real DB,
  runs the readiness gate, and warns loudly on empty/no-data (never silently
  fakes news). Spec-16 CLI contract verified.

### Regression Guards
- `test_readiness_gate_red_on_synthetic_shape` (old fixture shape FAILS gate),
  `test_readiness_gate_green_on_real_shape`, `test_causal_boundaries_exact`,
  `test_identical_timestamp_deterministic`, `test_future_event_strictly_invisible`,
  dataset-id-changes-with-news check.

### Verification
30 bridge tests + 181 Phase 12/13/13B tests green. `scripts/news_readiness_report.py`
writes `artifacts/model_generation/news_benchmark_readiness.{json,md}` — gate RED
until real news exists. Old benchmark report + manifest explicitly marked
`SYNTHETIC_NEWS_BENCHMARK` (spec 19).

---

## BUG-043 — NaN/Inf Passed Through News Normalization Into the Neural Vector

- **Status**: FIXED
- **Severity**: HIGH
- **Confidence**: HIGH (proven by adversarial test)
- **Discovered**: Phase 13B bridge contract tests (2026-08-17)
- **Verified**: `tests/unit/test_news_bridge_contract_phase13b.py::TestCategoricalSafety::test_nan_inf_never_enters_vector`

### Problem
`normalize_news_frame` left `float("nan")` / `float("inf")` values in float64
columns untouched — a NaN/Inf news feature would reach the training matrix
(BUG-041-class defect for the NewsContext vector).

### Root Cause
`_coerce_field` skipped columns already dtype f64/Float64 without checking
finiteness.

### Fix
`_coerce_field` now replaces non-finite float64 values with the safe default
0.0 (`pl.when(pl.col(col).is_finite())...otherwise(default)`).

### Regression Guards
- NaN/Inf in xauusd_relevance/bullish_pressure never enter the vector; all
  12 context values finite.

---

## BUG-044 — Windows float(numpy.datetime64)/Polars Scalar OSError in Epoch Extraction

- **Status**: FIXED
- **Severity**: MEDIUM (Windows-only crash)
- **Confidence**: HIGH (proven by repro)
- **Discovered**: Phase 13B bridge implementation (2026-08-17)
- **Verified**: `tests/unit/test_news_bridge_contract_phase13b.py::TestWindowsTimestampSafety`

### Problem
`float(value.timestamp())` on a Polars `datetime[us, UTC]` scalar (or numpy
datetime64) raised `OSError: [Errno 22] Invalid argument` on Windows during
`news_context_at` — crashing the entire benchmark dataset build.

### Root Cause
Polars scalars satisfy `hasattr(value, "timestamp")` but their `.timestamp()`
is not a usable Python-float API on Windows; numpy datetime64 the same.

### Fix
`_safe_epoch_sec` now calls `.timestamp()` ONLY on real `datetime.datetime`;
all other types (Polars scalar, numpy datetime64, ISO strings) are normalized
via their ISO string form → `datetime.fromisoformat` (naive treated as UTC).

### Regression Guards
- Polars scalar / numpy datetime64 / naive datetime / ISO string / None / NaT /
  garbage covered in 4 dedicated timestamp-safety tests.

---

## BUG-045 — Closed-Trade Outcomes Lost After Restart (request_id gap) + Protective Exits Mislabeled MANUAL_CLOSE + Zero-PnL Fallback Masking Missing Broker Outcome

- **Status**: FIXED (2026-08-17, Phase 14 forensic completion)
- **Severity**: CRITICAL (trading memory / learning path silently dropped closed trades)
- **Confidence**: HIGH (live artifacts/audit.db evidence + deterministic repro)
- **Discovered**: Phase 14 incident tickets 152487871408/152487871455/152487871322/152487871342/152487871361/152487871382
- **Verified**: `tests/unit/test_outcome_correlation_phase14.py` (26 tests) + runtime reproduction probe

### Symptom
1. `[EXPERIENCE] INVALID skipped outcome without request_id` for broker-closed tickets; the trade outcome never reached Strategy Intelligence.
2. `exit_mechanism=MANUAL_CLOSE` on rows where `was_sl_modified=True` and the state machine had reached PROFIT_TRAILING / risk-free territory.
3. `net_pnl=$0.00` (and `pnl=0.0, commission=0.0, swap=0.0`) on every closed row in `artifacts/audit.db` even though live floating PnL was +$9..+$13.
4. `initial_sl_price == final_sl_price` on every autopsy row — the SL modification timeline was destroyed.
5. Only 6 of 15 decision experiences ever received an outcome; tickets with empty `order_id` produced NO outcomes at all.

### Root Cause
1. **request_id lived only in the in-memory ticket map** `_entry_order_ids` (populated at dispatch). After a restart / reconciliation the map is empty; `_record_experience_outcome` read it, found nothing, logged INVALID and returned without recording — the outcome was silently discarded. There was no deterministic correlation fallback.
2. **MANUAL_CLOSE was a garbage-default**: the exit-mechanism resolution chain fell through to `ExitMechanism.MANUAL_CLOSE` for any close not forced/TP/SL-detected, including protective stops with deal_reason==3 + SL geometry.
3. **Zero-PnL fallback**: the close sweep used `matched_deal = next(deal for deal in history_deals if ...)` with `hours_back=1`. When no deal matched (restart, delayed sweep) `profit_usd` defaulted to `0.0` and the autopsy wrote zeros — a silent "unknown == zero" masquerade.
4. **SL timeline destroyed**: the in-loop modification detector overwrote `_entry_sls[ticket] = pos.sl` on every broker-side SL change, so the "SL at entry" was replaced by the current SL; the autopsy compared `initial == final` and concluded no modification.
5. **No missed-close self-heal**: close detection was purely "ticket disappeared from live positions"; a position closed while the engine was down was never discovered.

### Fix
1. **Deterministic outcome correlation** (`experience/outcome_recovery.py::resolve_outcome_correlation`):
   - ORIGINAL_REQUEST (request_id present) → key `exp_<request_id>`.
   - POSITION_STATE (request_id lost; immutable ledger holds the decision under the request/ticket identifiers) → recovered via `ExperienceLedger.get_experiences_by_order_id`.
   - BROKER_TICKET_FALLBACK (only the broker ticket exists) → deterministic `exp_bt_<ticket>` with explicit provenance.
   - Ambiguity → explicit CORRELATION_FAILED diagnostics, never silent reuse. `record_trade_outcome` now attempts recovery BEFORE declaring INVALID, and records `correlation_source`/`correlation_detail` in the outcome payload.
2. **Protective exit taxonomy** (`classify_exit_reason`): uses broker DEAL_REASON + SL/TP geometry + `was_sl_modified` + protective context → BREAK_EVEN_SL_HIT / TRAILING_STOP_HIT / HARD_SL_HIT / RISK_FREE_SL_HIT / TAKE_PROFIT_HIT / genuine MANUAL_CLOSE. A stop-out is never MANUAL_CLOSE merely because protection logic ran first. `accounting/normalize._MECHANISM_MAP` extended (BREAK_EVEN_STOP / TRAILING_STOP / STRATEGY_EXIT).
3. **Broker outcome reconstruction** (`reconstruct_broker_outcome`): aggregates ALL close deals per position (gross/commission/swap/volume/deal_ids; partial closes never double-counted); authoritative result comes from the deal path; missing deals are flagged `reconstruction_source=NONE` — never silently written as zero.
4. **SL timeline preserved**: `_entry_sls` stays frozen at the OPEN value; broker-side SL advances in `_last_modify_sl` + `_sl_modified_flags`. `final_sl_price` (broker-side at close) and `initial_sl_price` (at entry) now differ correctly.
5. **Reconciliation close-loop** (`OrderLifecycleManager.reconcile_missed_closes`): queries broker history, discovers closed tickets with an OPENED ledger placeholder but no close and no internal tracking (restart gap), restores the originating request_id from the OPENED row, and emits the same autopsy + experience outcome path. Wired before the dead-ticket sweep in `manage_active_positions` (runs even with zero open positions); `_reconcile_seen` dedups; fully exception-isolated.
6. **BREAK_EVEN is a first-class outcome** (`OutcomeClass` + `BREAKEVEN_R_BAND=0.05` matching the evaluator's threshold): zero/near-zero PnL outcomes are recorded, decomposed, attributed and counted (`breakeven_count`), never INVALID.

### Regression Tests (tests/unit/test_outcome_correlation_phase14.py, 26 tests)
- Break-even first-class classification + zero-pnl outcome recorded + strategy statistics (`breakeven_count`).
- Correlation recovery: ORIGINAL_REQUEST / POSITION_STATE / BROKER_TICKET_FALLBACK distinct provenance; ambiguity → None; missing request_id recovers and records.
- Exit taxonomy: BREAK_EVEN_SL_HIT / TRAILING_STOP_HIT / HARD_SL_HIT / TAKE_PROFIT_HIT / genuine MANUAL_CLOSE / forced override.
- Broker reconstruction: deal path provides realized PnL; multi-deal aggregation (no double count); no-deal → NONE source; BrokerOutcome round-trip.
- Idempotency: duplicate close callbacks → exactly one persisted outcome (DB UNIQUE authoritative).
- Multi-ticket independence: 5 tickets → 5 distinct experiences/outcomes.
- SL timeline: `_entry_sls` frozen at open, `_last_modify_sl` advances.
- Failure isolation: ledger failure returns False, never raises.
- Reconciliation: missed close recovered from broker history with request_id restored; second pass no duplicate.

### Verification
- 162 focused tests green (Phase-14 + experience + accounting + order lifecycle + adaptive + log-autopsy).
- Full unit suite green except pre-existing user-WIP `test_news_bridge_phase13b::TestBuildFrameFromDb::test_export_roundtrip` (untracked WIP, untouched by this fix).
- Runtime reproduction probe: OPEN → PROFIT → protective SL close → ledger CLOSED (TRAILING_STOP_HIT, real pnl/commission) → outcome (correlation_source=ORIGINAL_REQUEST, broker=BROKER_DEALS_AGGREGATED) → strategy stats updated — full chain without manual intervention.
- ruff check + ruff format --check clean on all changed files; mypy clean on all changed files (4 pre-existing mypy src errors are user-WIP: live_engine.py x2, server.py, news_bridge.py).

### Architectural Lessons / Regression Guards
- A closed trade is data: WIN / LOSS / BREAK_EVEN must ALL reach the experience ledger; zero PnL is an outcome class, not an invalidation.
- Correlation identity must survive restart: never rely solely on an in-memory ticket→request map; the immutable ledger is the position-state authority for recovery.
- Missing data (no broker deal) must be flagged unknown (`reconstruction_source=NONE`), never silently written as zero.
- Protective exits must be classified from broker evidence + SL geometry, never defaulted to MANUAL_CLOSE.
- Reconciliation must run BEFORE the dead-ticket sweep so the `_entry_timestamps` guard prevents the async-write race that double-records closes.

## BUG-046 — GET /api_client.js 404 → `Uncaught ReferenceError: NX is not defined` at app.js:402

- **Status**: FIXED (2026-08-17, Phase 14 completion)
- **Severity**: HIGH (frontend boot failure — dashboard rendered static HTML only)
- **Confidence**: HIGH (reproduced in browser console + TestClient 404)
- **Verified**: `tests/unit/test_frontend_assets_phase14.py` (NX namespace contract)

### Symptom
Browser console: `GET /api_client.js 404 (Not Found)` then `Uncaught ReferenceError: NX is not defined at initApp (app.js:402)`. Chart bootstrap and every `NX.api.get(...)` call after it died; the dashboard never rendered live state.

### Root Cause
`Web/api_client.js` defines `window.NX` (the central API client with request correlation + safe error parsing introduced in BUG-040) and `index.html:1483` loads it before `app.js` — but `server.py`'s static-route list (`/`, `/styles.css`, `/app.js`) omitted `/api_client.js`. The browser received a 404 HTML body as the script, so `window.NX` was never defined when `app.js` executed. The frontend module existed and was required (23 `NX.` call sites in app.js); it was simply not served.

### Fix
Added `@app.get("/api_client.js")` → `FileResponse(WEB_DIR / "api_client.js")`. No fake `const NX = {}` shim was introduced (guard test asserts the real client is served, not a stub).

### Regression Guards
- `test_api_client_served` — route returns 200
- `test_api_client_defines_window_nx` — file defines `window.NX`
- `test_index_script_order_nx_before_app` — api_client.js loads before app.js
- `test_no_fake_nx_namespace_in_app_js` — no fake namespace in app.js

### Verification
`pytest tests/unit/test_frontend_assets_phase14.py` → 24 passed. Browser smoke: all 5 local assets + 8 webfonts serve 200; 137/137 DOM ids resolve.

---

## BUG-047 — Tailwind Play CDN Runtime Dependency (`cdn.tailwindcss.com`)

- **Status**: FIXED (2026-08-17, Phase 14 completion)
- **Severity**: MEDIUM (external network dependency for basic UI rendering + production warning)
- **Confidence**: HIGH
- **Verified**: `tests/unit/test_frontend_assets_phase14.py::TestTailwindLocalBuild`

### Symptom
`index.html` loaded `https://cdn.tailwindcss.com` (JIT Play CDN) + inline `tailwind.config`. Browser warned "cdn.tailwindcss.com should not be used in production"; dashboard styling broke offline.

### Root Cause
The CDN script was the only Tailwind source; no compiled artifact existed in the repo.

### Fix
- `tailwind.config.js` — theme colors preserved, `content: ["./Web/index.html", "./Web/*.js"]`
- `Web/tailwind_input.css` — `@tailwind base/components/utilities`
- Compiled locally: `npx tailwindcss -c tailwind.config.js -i Web/tailwind_input.css -o Web/tailwind.css --minify` (29,494 bytes), served at `/tailwind.css`
- FontAwesome 6.4.0 also localized under `Web/vendor/fontawesome/` + `Web/vendor/webfonts/` (2 new server routes); **zero CDN refs remain** in index.html

### Regression Guards
- `test_no_tailwind_cdn` — no cdn.tailwindcss.com / https://cdn. in index.html
- `test_compiled_tailwind_css_exists` + `test_compiled_tailwind_served` — artifact exists + served
- `test_tailwind_css_contains_used_colors` — compiled CSS carries the theme palette
- `test_index_has_no_broken_local_refs` — every local ref serves 200

---

## BUG-048 — Chart History Served In-Memory Aggregator Instead of Authoritative MT5 History

- **Status**: FIXED (2026-08-17, Phase 14 completion)
- **Severity**: HIGH (chart could not render before ticks flowed; no real broker candles)
- **Confidence**: HIGH (proven with real MT5 `copy_rates_from_pos` on live terminal)
- **Verified**: `tests/unit/test_mt5_status_endpoint.py::test_chart_history_paper_source`, `test_frontend_assets_phase14.py::TestChartHistoryContract`; real-terminal pipeline probe

### Symptom
`/api/chart/history` returned the engine's in-memory aggregator bars (empty until live ticks processed); there was no path to official broker rate history, so the chart stayed blank after cold start.

### Root Cause
The endpoint wrapped `get_system_state().bars` (aggregator) and had no `copy_rates_*` provider wiring.

### Fix
`/api/chart/history` now calls `engine.adapter.get_rate_history()` (official `copy_rates_from_pos`/`copy_rates_range`, UTC-normalized, OHLC-validated) as the authoritative source; engine bars only as explicit `ENGINE_STATE` fallback. Response carries diagnostics: `source/symbol/timeframe/requested/returned/first_timestamp/last_timestamp/generated_at/error`; bars carry `time/open/high/low/close/tick_volume/spread/real_volume/is_complete`. Server logs `[MT5_CHART] event=HISTORY_LOADED`.

### Real-MT5 verification (2026-08-17)
`source=MT5 bars=250 requested=250 returned=250 first=2026-08-17T02:10:00+00:00 last=2026-08-17T06:19:00+00:00` — real gold M1 candles.

---

## BUG-049 — MT5 order_calc_profit/order_calc_margin Called With Keyword Arguments (TypeError)

- **Status**: FIXED (2026-08-17, Phase 14 completion)
- **Severity**: HIGH (broker-native calculation APIs completely broken)
- **Confidence**: HIGH (reproduced on real terminal: `order_calc_profit() takes no keyword arguments`)
- **Verified**: `tests/unit/test_mt5_providers_phase14.py::TestRiskBrokerProvenance`; real-terminal calc smoke

### Root Cause
The MetaTrader5 Python binding exposes `order_calc_profit`/`order_calc_margin` as positional-only builtins. The adapter called them with kwargs, raising `TypeError` on every invocation; the real-account smoke showed `order_calc_profit: FAILED: TypeError`.

### Fix
Calls converted to positional args; result is `BROKER_NATIVE` value (real: profit(0.01 lot +$1.50)=1.5, margin(0.01@2000)=20.0, margin(0.01@~4390)=43.91 on the demo account).

### Regression Guards
- All `TestRiskBrokerProvenance` tests assert BROKER_NATIVE/FALLBACK_ESTIMATE provenance.
- The `/api/mt5/status` `calculations` block returns `source=BROKER_NATIVE` from the live terminal.

---

## BUG-050 — Circular Import: ports.mt5_port ↔ adapters.mt5 (Collection Errors Across Unit Suite)

- **Status**: FIXED (2026-08-17, Phase 14 completion)
- **Severity**: HIGH (5 test modules failed collection; engine import chain broken)
- **Confidence**: HIGH
- **Verified**: full `pytest tests/unit` collection now proceeds (no ImportError)

### Symptom
`ImportError: cannot import name 'IMT5Port' from partially initialized module 'nexus_scalp.ports.mt5_port' (circular import)` — broke collection of `test_adaptive_position_management.py`, `test_execution_architecture.py`, `test_hardened_protocol.py`, `test_htf_warmup_gate.py`, `test_log_autopsy_fixes.py`, and any module importing `order_manager`.

### Root Cause
`ports/mt5_port.py` gained runtime imports from `nexus_scalp.adapters.mt5.providers` + `.diagnostics`; importing the submodule executes `adapters/mt5/__init__.py`, which eagerly re-exported `IMT5Port` from the still-initializing port module → cycle.

### Fix
`adapters/mt5/__init__.py` now performs NO eager port re-export (docstring documents the contract). No code imported the package-level symbols, so nothing else changed.

### Regression Guards
- `python -c "from nexus_scalp.execution.order_manager import OrderLifecycleManager"` imports clean
- Full unit suite collects without ImportError

---

## BUG-051 — SSE Endpoint Unsable by Sync TestClient/ASGITransport (endless-stream harness hang)

- **Status**: FIXED (2026-08-17, Phase 14 completion) — test-harness defect, not a server defect
- **Severity**: MEDIUM (test suite hung; browser EventSource unaffected)
- **Confidence**: HIGH (sync + async harness both blocked at `client.stream`)
- **Verified**: `tests/unit/test_web_security.py::test_07_sse_payload_has_no_traceback` now passes in ~1.3s

### Symptom
`test_07_sse_payload_has_no_traceback` hung indefinitely: httpx sync TestClient and ASGITransport both block until response completion, which never happens for an endless SSE generator.

### Root Cause
Test-harness/toolchain limitation (httpx 0.28 + starlette 1.3 mid-transition; sync `client.stream()` never returns for streaming responses). The SSE endpoint itself is correct for browsers (EventSource).

### Fix
Test rewritten to drive a real uvicorn server on an ephemeral port (`port=0`) and read the first SSE frame via a bounded raw socket read with a 15s deadline — verifying status 200, no traceback/RuntimeError/file:// in the frame body, and `engine_running` in the JSON payload.

### Regression Guards
- The test now completes in seconds (was indefinite hang)
- Bounded socket read ensures the suite can never block forever on SSE again
- WebSocket/SSE server behavior unchanged (still endless stream for EventSource)

### Also fixed in the same pass
- `test_08` legacy-pattern regex over-broad: `detail\s*=\s*f?['"][^'"]*\{?e\}?[^'"]*['"]` matched ANY literal string containing the letter `e` (e.g. `detail = "no engine"`). Tightened to the actual f-string exception-interpolation shape (`detail=f"...{*err*}"`) — security intent preserved, false positives removed.

---

## BUG-052 — Runtime Mode Displayed From Config Only (LIVE shown while MT5 disconnected)

- **Status**: FIXED (2026-08-17, Phase 14 completion)
- **Severity**: MEDIUM (dashboard could lie about LIVE state)
- **Confidence**: HIGH
- **Verified**: `tests/unit/test_mt5_status_endpoint.py::test_live_configured_but_mt5_disconnected`

### Symptom
Header MODE selector showed `LIVE TRADING` whenever config said LIVE, regardless of MT5 connection.

### Fix
- `LiveEngine._update_runtime_mode()` derives the REAL mode from connection state + account `trade_allowed`: `LIVE` / `LIVE / TRADE_BLOCKED` / `LIVE_CONFIGURED / MT5_DISCONNECTED` / PAPER / SHADOW / STOPPED; refreshed on connect + 5s throttle.
- `/api/status` exposes `runtime_mode`; the UI renders a colored badge next to the selector (green only when truly connected, red on degraded).

### Regression Guards
- Forced-disconnect adapter (config LIVE, adapter disconnected) → `runtime_mode` contains `DISCONNECTED`, bid/account None (no fake data)
- Connected paper adapter → `runtime_mode == "PAPER"`

---

## BUG-053 — Account Position View Restricted to Bot Filter (XAUUSD + magic 888101)

- **Status**: FIXED (2026-08-17, Phase 14 completion)
- **Severity**: MEDIUM (account-wide views silently dropped non-bot positions)
- **Confidence**: HIGH
- **Verified**: adapter `get_all_positions()` + `/api/mt5/status` positions block + accounting `live_state`

### Root Cause
`DirectMT5Adapter.get_positions()` hard-filters `pos.symbol == "XAUUSD" and pos.magic == 888101` — correct for the bot's own management path, wrong for account-wide views.

### Fix
Added `get_all_positions()` (no filter) consumed by the dashboard positions list and `/api/mt5/status`; the classic `get_positions()` keeps the bot filter for `OrderLifecycleManager` position management (task §22: separate ALL / BOT / SYMBOL views).

### Regression Guards
- `/api/mt5/status` `positions` reflects all account positions
- `AccountingCore.live_state()` open-position count unaffected (uses adapter get_positions)
---

## BUG-054 — Model/Regime Blindness in Position Management (AI-Flip Exit Dead Code)

- **Status**: FIXED (2026-08-17, Phase 15 exit-behavior audit)
- **Severity**: HIGH (in-trade exits never saw the live model or current regime)
- **Confidence**: HIGH
- **Verified**: `tests/unit/test_exit_behavior_forensic.py::test_d1_strong_reversal_while_buy_exits`, `::test_d3_ai_probability_flip_visible_in_evidence`, `::test_d4_regime_invalidation_exit`

### Root Cause
`live_engine._process_tick_pipeline` called `manage_active_positions(symbol, current_tick, feature_vector, symbol_info, account)` WITHOUT `probs` or `regime_state`. Consequences (all evidence-backed from artifacts/audit.db + nse_live.log):
- The OLM "AI DIRECTION FLIP & FAST REVERSAL" block (`if probs is not None:`) was DEAD CODE in production — never triggered.
- `_calculate_adaptive_evidence_scores` degraded to static feature heuristics; the model was never consulted on open positions.
- The giveback-protection `regime=` argument received the regime at ENTRY (stale snapshot), so VOLATILITY_EXPANSION close-suppression never reacted to a CURRENT regime change.

### Fix
- `live_engine.py` now computes `probs_for_mgmt` (inference result when the warmup gate is READY, else None — protective stops never pause) and threads BOTH `probs` and `regime_state` into `manage_active_positions`.
- OLM `_current_regime_str()` resolves the CURRENT regime from the threaded `regime_state`, falling back to the entry snapshot only when no live regime is available.
- `[POSITION_EXIT_EVAL]` structured logs record decision, reasons, hold_score, regime (current vs entry), elapsed_sec — never per-tick spam.

### Regression Guards
- Strong reversal while BUY with sell-bias ≥ 0.60 → close + `ExitMechanism.AI_REVERSAL_EXIT`
- AI probability flip → adverse_score > 0.6 / continuation_score < 0.3 in evidence
- Regime invalidation combined with adverse excursion → thesis-invalidated exit
- Healthy continuation → HOLD (no panic close)

---

## BUG-055 — LOSS_HARD_EXIT Arbitration Gap (State Reached, Close Never Dispatched)

- **Status**: FIXED (2026-08-17, Phase 15 exit-behavior audit)
- **Severity**: CRITICAL (flagship ticket 152488669567 sat in LOSS_HARD_EXIT ~2 min at -$105..-$171, closed only when price hit SL)
- **Confidence**: HIGH
- **Verified**: `tests/unit/test_exit_behavior_forensic.py::test_r1_critical_hold_score_dispatch_close`

### Root Cause
The state machine reached `LOSS_HARD_EXIT` (`[HYSTERESIS BYPASS - EMERGENCY TRANSITION]`) but the `[EXIT TRACE] LOSS_HARD_EXIT triggered` line NEVER followed and no broker close was dispatched. The adaptive state was being evaluated but the Level-2 arbitration path that maps `LOSS_HARD_EXIT → "CLOSE"` never executed on the live path.

### Fix
- `_arbitrate_decision` Level-2 now emits `[EXIT TRACE] LOSS_HARD_EXIT triggered` and returns `("CLOSE", "LOSS_HARD_EXIT: recovery budget exhausted or adverse pressure too high")` — the close is dispatched through the standard broker-close path with 3-retry adapter.
- 60-second minimum-survival grace is preserved: `LOSS_HARD_EXIT`/`LOSS_EXIT_PRESSURE` are downgraded to `LOSS_RECOVERY_CANDIDATE` only while `duration_sec < 60.0` (a fresh position must breathe the spread); the first-observation emergency bypass (already present, see BUG-018) is unchanged.

### Regression Guards
- Losing position, hold_score < 30, age > 60s → broker `close_position` dispatched (mock adapter records ticket)
- Claim: `LOSS_HARD_EXIT` arbitration now always produces a CLOSE verdict past the grace period

---

## BUG-056 — Min-Loss EV Inversion (Exit Never Fires on Deep Drawdown)

- **Status**: FIXED (2026-08-17, Phase 15 exit-behavior audit)
- **Severity**: HIGH (deep underwater losers held to full SL; flagship 152488669567 MAE -$180.78 vs initial risk $196.88)
- **Confidence**: HIGH
- **Verified**: `tests/unit/test_exit_behavior_forensic.py::test_r4_min_loss_ev_fires_on_deep_drawdown`, `::test_r4b_ev_does_not_fire_on_deep_drawdown_with_strong_recovery`

### Root Cause
`_evaluate_minimum_loss_optimization` computed:
```python
expected_recovery_value = max(15.0, abs(current_pnl_usd) * 2.0)
expected_additional_loss = max(1.0, initial_risk_usd - abs(current_pnl_usd))
```
As the drawdown deepened, the recovery payoff GREW with the loss while the expected additional loss SHRANK (the hard stop was already mostly consumed) — so `ev_hold` became MORE positive the deeper the loss. Verified: ticket 152488669567 at pnl -171.12, risk 196.88, rec 0.204, adv 0.542 → EV +55.86 vs threshold -29.53 → never breached; the trade closed at full SL.

### Fix
The recovery payoff is now anchored to the PLANNED reward objective at entry time, never the current loss:
```python
planned_rr = float(getattr(self.algo_config, "min_risk_reward_ratio", 1.8) or 1.8)
expected_recovery_value = max(15.0, initial_risk_usd * planned_rr)
expected_additional_loss = max(1.0, initial_risk_usd)
```
EV now decreases monotonically as the drawdown deepens (payoff fixed, adverse probability dominates), so the minimum-loss exit fires when holding is statistically unjustified, while strong recovery evidence still holds the position.

### Regression Guards
- Flagship numbers (pnl -171.12, risk 196.88, rec 0.204, adv 0.542, age > 60s) → EV breach must fire
- Same drawdown with strong recovery (rec 0.75, adv 0.15) → MUST NOT fire (no false panic)
- 60s grace period still suppresses EV exits on fresh positions

## BUG-057 — Audit DB Pathological Growth (79MB/Day from Disposable Telemetry)

- **Status**: FIXED (2026-08-17, Audit DB cleanup + root-cause fixes)
- **Severity**: MEDIUM-HIGH (79MB audit.db after ~14h; unbounded growth, no retention)
- **Confidence**: HIGH (measured: audit_signals 67.9MB / 33,193 rows, position_lifecycle_events 12.2MB / 8,954 rows in <14h)
- **Verified**: `tests/unit/test_audit_db_growth_bug054.py` (6 tests: dedup, telemetry, risk preservation, payload, purge)

### Root Cause
Two over-logging defects with no retention policy:
1. `log_signal` persisted a FULL ~1.2KB JSON proposal payload per signal AND ~17% of it re-duplicated existing structured columns (`buy_probability`, `model_buy_probability`, `ai_buy_probability` all the same value; `regime`/`confidence`/`action` present in both row and payload). The in-memory `_last_logged_signal_key` dedup was restart- and race-unsafe.
2. 16,567 `TICK_DUPLICATE_SUPPRESSED` + 4,425 `ORDER_FREQUENCY_THROTTLED` rows (63% of all signals) were persisted as HEAVY rows even though the engine itself classified them as duplicates/throttles — pure telemetry noise at ~1.2KB each.
3. `position_lifecycle_events.POSITION_MOVING` fired on every 15%-of-risk price drift (7,815 rows / 183 positions = ~43 per position), each re-persisting full market_context + position_snapshot + payload.
4. No purge/retention existed anywhere in the codebase.

### Fix
- **Persistent dedup (DB-enforced):** `audit_signals.signal_dedup_key` (deterministic sha256 of symbol|M1-candle|model_action|decision_stage|execution_mode|reason_code) + UNIQUE index + `ON CONFLICT DO NOTHING` in the worker insert. No SELECT-then-INSERT, no in-memory state, restart/race-safe.
- **Guard telemetry:** `TICK_DUPLICATE_SUPPRESSED` / `ORDER_FREQUENCY_THROTTLED` now aggregate into `audit_guard_telemetry` (window_start, symbol, reason_code, count — one UPSERT row per minute) instead of heavy signal rows.
- **Lean payload:** payload reduced to 8 approved forensic fields (model_action, ai_buy/sell/no_trade probabilities, regime_confidence, risk_allowed, guardian_status, rejection_reason); full proposal dump and duplicate probability fields removed. ~1.2KB → ~250B.
- **POSITION_MOVING throttle:** persist only when ≥60s elapsed, SL/TP changed, or ≥15% risk drift; tracks last SL/TP/ts per ticket. 7,815 → ~1/min/position bound.
- **Retention:** `AuditRepository.purge_old_audit_data()` — bounded (500-row) batched deletes for signals >7d, MOVING >3d, guard telemetry >13d; NEVER touches ledger/experiences/autopsies/research. CLI: `nse audit-purge [--signal-days N] [--moving-days N] [--json]`.
- **MAX_EXPOSURE_REACHED preserved** as a real audit row (risk-engine evidence, spec §9).

### Regression Guards
- Same decision twice (different request_id) → exactly 1 row
- TICK_DUPLICATE_SUPPRESSED × 5 → 0 signal rows, telemetry count = 5
- MAX_EXPOSURE_REACHED → still a signal row (risk evidence)
- Payload has only approved keys, < 400 bytes
- Purge removes old signal/MOVING, keeps fresh, never touches audit_ledger

---

## BUG-058 — Chart/Engine Desync After 5-6h Downtime (Stale Aggregator + 250-Bar UI Cap)

- **Status**: FIXED (2026-08-17, Phase 15b visualizer resync)
- **Severity**: HIGH (chart and engine engines diverged from broker reality after downtime; duplicate stale minutes, blank/stale canvas, truncated history)
- **Confidence**: HIGH (code-path verified: `_cold_start_warmup` blind-append + forming-bar duplication; `get_system_state` stale ServerState preference; 250-bar hard cap)
- **Verified**: `tests/unit/test_chart_resync_phase15b.py` (13 tests) + `tests/unit/test_frontend_assets_phase14.py::TestChartResyncContract` (3 tests)

### Symptom
After the bot shuts down for 5-6 hours and comes back:
1. The first live tick created a DUPLICATE stale completed candle at the same timestamp as the broker's still-forming minute (blind `_completed_bars.append` in `_cold_start_warmup`).
2. The forming bar started from the live price instead of the historical open of the current minute → wrong OHLC for the whole minute.
3. The UI chart stayed truncated at 250 candles (`-250:` slices in engine + server) — a 5-6h M1 session is 300+ bars, so the full session could never repaint.
4. `/api/status` preferred `ServerState.bars` (stale from the pre-downtime process) over the freshly reseeded aggregator (`if real_bars:` without freshness/age check).

### Root Cause
- `BarAggregator` had no reseed primitive — history could only be appended, and appending broker history that INCLUDED the current (still forming) broker minute caused `process_tick` to emit a second completed bar for the same minute on the first live tick.
- `_cold_start_warmup` features were rebuilt from the appended bars but the aggregator forming bar was never aligned to the broker's latest minute.
- The watchdog reconnect path and `/api/chart/history` REST path had no engine-side resync.
- All ServerState pushes and the frontend bootstrap were hard-capped at 250 bars.

### Fix
1. **`BarAggregator.reseed(completed_bars)`** (new primitive): atomically replaces history with broker-authoritative bars: dedupe by timestamp, sort ascending, drop incomplete bars, and align the forming bar (open/high/low/close/volume) to the LATEST broker minute so the first live tick continues it instead of duplicating it.
2. **`LiveEngine._cold_start_warmup`**: now calls `aggregator.reseed(hist_m1_bars)` instead of blind append; pushes ServerState visuals unconditionally (900 bars) after seeding.
3. **`LiveEngine._resync_from_broker(symbol)`** (new): broker-authoritative reseed used by the tick-watchdog reconnect path — refetches 3500 M1 bars, reseeds, rebuilds a causal feature record, pushes ServerState, re-evaluates warmup readiness. Fully failure-isolated (never stops the loop).
4. **`LiveEngine.sync_chart_state()`** (new): pushes the current aggregator series (last 900) + real SMC overlays to ServerState; used after every reseed and lazily by the REST layer.
5. **`/api/chart/history`**: default window 250 → **900** (`count` query param, bounded 1..5000); a successful broker fetch now ALSO reseeds the engine aggregator + ServerState so snapshot/SSE/overlays converge instantly (`source=MT5` + engine mirror).
6. **`get_system_state`**: prefers `real_bars` only when `len >= 100` (stale/empty ServerState falls through to the live aggregator), and the aggregator fallback window is 900.
7. **Frontend (`app.js` + `index.html`)**: 900-bar render support (crosshair/tooltip/auto-fit already index-based → length-agnostic), new **Resync** button (`resyncChart()` → `/api/chart/history?count=900`), auto-resync on every SSE reconnect (10s throttle) and by the 30s stale watchdog (30s throttle).

### Regression Guards
- `test_reseed_replaces_history_and_aligns_forming_bar` — forming bar continues the broker's latest minute
- `test_reseed_dedupes_and_sorts` / `test_reseed_empty_clears_state` / `test_reseed_drops_incomplete_bars`
- `test_reseed_then_live_tick_continues_bar` — first live tick of the seeded minute does NOT mint a duplicate; next minute emits exactly ONE new bar
- `test_resync_from_broker_reseeds_and_pushes_visuals` / `test_resync_from_broker_skips_when_no_bars`
- `test_sync_chart_state_pushes_900_bars` — 1200 available → exactly 900 pushed
- `test_chart_history_default_window_is_900` / `test_chart_history_custom_count_honored` / `test_chart_history_resync_mirrors_bars_into_engine`
- `TestChartResyncContract` — Resync button present, `resyncChart()` defined, wired to reconnect + stale watchdog

### Relevant Files
- `src/nexus_scalp/market_data/bar_aggregator.py`
- `src/nexus_scalp/application/live_engine.py`
- `src/nexus_scalp/web/server.py`
- `Web/app.js`, `Web/index.html`
- `tests/unit/test_chart_resync_phase15b.py`, `tests/unit/test_frontend_assets_phase14.py`

### Architectural Lessons / Regression Guards
- History ingestion must be REPLACE + ALIGN, never blind-append: broker rate history includes the still-forming minute, and appending it corrupts the aggregator's bar-boundary logic on the next tick.
- Every ServerState consumer must be freshness-aware: a stale pre-downtime chart cache must never outrank a freshly reseeded aggregator.
- UI data windows must be derived from the maximum expected session length (a full 24h M1 day = 1440 bars), not an arbitrary painter's shortcut.

## BUG-059 — Web UI Cannot Save Telegram Token/Admin + Missing Telegram Reporting Hooks

- **Status**: FIXED (2026-08-17, web UI + Telegram reporting expansion)
- **Severity**: MEDIUM (config save appeared broken; no test-message path; no reports for purge/warmup/daily summary)
- **Confidence**: HIGH (root cause proven: engine/web-server not running → browser fetch NETWORK_ERROR; backend save logic verified working in isolation)
- **Verified**: `tests/unit/test_telegram_reporting_bug057.py` (6 tests) + live integration check (GET/POST /api/config, POST /api/telegram/test, disabled/no-notifier envelopes)

### Root Cause
1. **Save failure**: the web UI is served by the engine's embedded uvicorn (127.0.0.1:8080). When the engine isn't running, every fetch throws `TypeError: Failed to fetch` → the UI's `NX.api` helper reports "Network request failed (request req_...)" with no hint about the real cause. The `/api/config` backend itself was proven correct (GET/POST both succeed against a live app). The user's stale browser tab hit a dead server.
2. **No Telegram test path**: the settings form had Enable/Token/Admin fields but no way to validate them.
3. **Missing report hooks**: important engine events (audit purge results, warmup→READY transition, daily performance) had no Telegram notification.

### Fix
- **Web UI**: `saveConfiguration()` now distinguishes server-unreachable (actionable: "Is the engine running? nse run → http://127.0.0.1:8080") from real backend errors; added a "Send Test Message" button (POST /api/telegram/test) with inline status.
- **Backend**: new `POST /api/telegram/test` endpoint — validates notifier availability/enabled, sends via `notify_test_message()`, returns `{success, message_id}` or error envelope (`NOTIFIER_UNAVAILABLE` / `NOTIFIER_DISABLED` / `SEND_FAILED`).
- **Notifier**: new templates — `notify_test_message`, `notify_engine_stopped`, `notify_engine_error` (CRITICAL), `notify_audit_purge`, `notify_warmup` (one-shot on NOT_READY→READY), `notify_daily_summary` (from accounting core PeriodKind.DAY).
- **Engine wiring**: audit purge result → `notify_audit_purge` on every 6h run; warmup READY transition → `notify_warmup` (guarded so it fires once); daily summary throttled to 24h → `notify_daily_summary`. All failure-isolated, never on the tick path.
- **Phase 15 wiring preserved**: restored `probs_for_mgmt` threading into `manage_active_positions` (was lost in a working-tree reset mid-session; recovered verbatim from session history and re-verified with the exit-behavior regression suite).

### Regression Guards
- `test_telegram_reporting_bug057.py`: test/stopped/error/purge/warmup/daily templates dispatch with correct severity; bot token redacted from bodies; endpoint contract present.
- Integration: save config → test message → disabled → no-notifier envelopes all correct.
- `test_exit_behavior_forensic.py` + `test_intelligence_phase09.py` + `test_accounting_core.py` + `test_audit_db_growth_bug054.py` + `test_htf_warmup_gate.py` all green (117 tests).

---

## BUG-059 — beforePush.ps1 False-Success on Test Failure (Native-Exe Exit Code Swallowed)

- **Status**: FIXED (2026-08-17, pre-push gate hardening)
- **Severity**: HIGH (gate script reported "ALL CHECKS PASSED" while pytest had FAILED tests)
- **Confidence**: HIGH (reproduced: full-suite run with `test_06_server_log_contains_detailed_exception` failing printed "🎉 ALL CHECKS PASSED" + exit 0)
- **Verified**: `beforePush.ps1` end-to-end run after fix — EXIT_CODE=0 only when pytest genuinely passes

### Root Cause
PowerShell does NOT throw a terminating error when a NATIVE executable (pytest/mypy/ruff) exits non-zero inside `try { } catch { }`. The original script wrapped every gate in try/catch, so a pytest failure was swallowed and the script printed the success banner and returned 0 — the CI/CD gate silently reported green on red builds.

### Fix
All four gates now call the native tool with `& tool ...` and check `$LASTEXITCODE` explicitly:
```powershell
& pytest tests/unit/ -q --tb=short
$pytestExit = $LASTEXITCODE
if ($pytestExit -ne 0) { Write-Failure "pytest exited with code $pytestExit ..." }
```
Same pattern for `ruff check`, `ruff format`, `mypy`.

### Regression Guards
- pytest exit != 0 → `Write-Failure` + `exit 1` (never the success banner)
- `beforePush.sh` (bash) already checked exit codes correctly (`if pytest ...; then`) — confirmed no change needed

---

## BUG-060 — test_06 Server-Log Flake (structlog PrintLogger vs stdlib Capture)

- **Status**: FIXED (2026-08-17, test determinism hardening)
- **Severity**: MEDIUM (order-dependent test flake in full-suite runs)
- **Confidence**: HIGH (root-caused: fresh pytest session has no `configure_logging()`, so structlog uses its DEFAULT `PrintLoggerFactory` — logs write to stdout and never reach stdlib `logging` handlers, so a capture-handler assertion sees nothing)
- **Verified**: `test_web_security.py` passes standalone AND after other suites (full 791-test run green)

### Root Cause
`test_06_server_log_contains_detailed_exception` used `contextlib.redirect_stdout` + a stdlib capture handler. In a fresh pytest process structlog is NOT configured (no conftest calls `configure_logging()`), so `structlog.get_logger()` returns a PrintLogger-backed proxy — records never reach `logging.Logger` handlers, and the test's handler saw zero records. Order-dependent: passed when an earlier test had initialized stdlib logging, failed in isolation/full runs.

### Fix
The test now calls `configure_logging(log_to_file=False)` first (rebuilds the stdlib pipeline, idempotent), suppresses `logging.raiseExceptions` (the rich ConsoleRenderer re-raises the probe exception while formatting exc_info, which otherwise propagates out of the test), attaches the capture handler to BOTH root and the named logger, and restores the pre-test root-handler set + level + raiseExceptions in `finally`.

### Regression Guards
- Test passes in isolation (fresh pytest, no prior configure_logging)
- Test passes after other suites that DO configure logging (order-independent)
- Original assertions preserved: SECRET_INTERNAL_MARKER_PATH, RuntimeError, WEB_ERROR all present in captured log output

## BUG-061 — Local Candle Intelligence Module (Candle-Close Gate) Added

- **Status**: FIXED (2026-08-17, new isolated subsystem)
- **Severity**: N/A (new capability)
- **Confidence**: HIGH (21 unit tests green; full suite green)
- **Verified**: `tests/unit/test_candle_intel_classifier_patterns.py` + `tests/unit/test_candle_intel_decision_store.py`

### What was added
Fully local, isolated, database-backed candlestick analysis + trade-decision
module at `src/nexus_scalp/candle_intelligence/`:
- **classifier.py** — CandleCloseClassifier: close-quality GATE. Computes body
  / upper-wick / lower-wick ratios, close-position-in-range, close strength,
  rejection / continuation / reversal / indecision / momentum-decay scores;
  classifies BULLISH/BEARISH continuation, reversal, INDECISION, TRAPPED_BREAKOUT,
  EXHAUSTION, FALSE_BREAKOUT, WEAK_CLOSE, INVALID. NaN/Inf/non-positive range
  -> INVALID (recorded, never crashes).
- **patterns.py** — PatternEngine: all 29 required patterns (hammer family,
  engulfing, stars, doji family, soldiers/crows, harami, cloud cover/piercing,
  three methods, double top/bottom, H&S, flag/pennant/wedge/triangle, gap) with
  raw shape fidelity + multi-factor context weighting (trend, volatility,
  structure, sweep proximity, spread/ATR) -> confidence [0,1].
- **decision.py** — CandleDecisionEngine: rule hierarchy (1 hard safety veto,
  2 regime filter, 3 candle-close validation, 4 pattern confirmation, 5 risk
  sizing, 6 execution). Outputs entry_allowed / hold_allowed / fast_exit /
  exit / modify / cancel with reason codes; weak/contradictory closes block
  entry and accelerate exit.
- **store.py + store_writes.py** — isolated SQLite (artifacts/candle_intel.db):
  12 tables (candles, candle_closures, candle_patterns, market_regimes,
  feature_vectors, trade_proposals, trade_decisions, open_positions,
  exit_signals, risk_evaluations, rule_vetoes, audit_log), every record with
  ts/symbol/timeframe/regime/pattern_name/pattern_score/
  candle_close_classification/decision_type/risk_state/reason_codes/
  raw_payload/computed_payload. Bounded reads, integrity check, deterministic
  JSON serialization. NO network, NO cloud, NO remote telemetry.
- **engine.py** — CandleIntelligenceEngine orchestrator: ingest_bar /
  process_candle_close; produces the spec §11 output contract; failure-isolated.
- **Config** — `candle_intel:` section in base.yaml (optional, defaults
  conservative: weak close blocks entry, entry gate 0.62).
- **Live wiring** — live_engine constructs the engine (failure-isolated) and
  feeds every completed M1 bar via `_on_new_bar`; decision exposed as
  `_last_candle_decision`.

### Safety
- Holds no adapter / order manager / risk engine; can never place/modify/close.
- Deterministic for identical input states; all intermediate+final results
  persisted locally; every veto/entry/hold/exit decision explainable via
  reason codes stored in rule_vetoes + trade_decisions.

### Performance follow-up (2026-08-17, same session)
The original store wrote synchronously to SQLite on every record_* call — a
latency risk on the tick path. Reworked to a RAM-first design:
- All `record_*` methods now enqueue onto an in-memory ring buffer + a bounded
  write queue (O(1), ~15 µs/op, NO disk I/O on the caller's thread).
- A dedicated background worker thread (`candle_intel_writer`) drains the queue
  into SQLite in batched transactions (WAL, `max_batch_size` rows/commit,
  0.3s flush interval).
- `query_recent` serves from RAM first (measured 0.03 ms), falling back to
  SQLite only for history/restart.
- Verified: `tests/unit/test_candle_intel_perf.py` (200 enqueues = 3 ms;
  persisted rows = 10 after flush; RAM read = 0.03 ms).

---

## BUG-062 — Duplicate Orders from Blind Adapter Retries (order_send sent twice + non-idempotent close/pending retry)

- **Status**: FIXED (2026-08-17, idempotency audit)
- **Severity**: HIGH (real duplicate pending orders / double closes on ambiguous retcodes)
- **Confidence**: HIGH (code-verified; user-reported "5-6 orders" symptom)
- **Verified**: `src/nexus_scalp/adapters/mt5/mt5_adapter.py` — `place_pending_order` L1073-74 contained
  `result = mt5.order_send(request)` **twice in a row** (same request, guaranteed double-send risk).

### Root Cause
1. `place_pending_order`: literal **duplicate `order_send` call** — the same pending-order
   request dispatched twice; either call can succeed → duplicate LIMIT/STOP orders.
2. `close_position`: 3-attempt retry re-sent the close **blindly after any non-DONE retcode**.
   If the broker accepted the first close but the response was ambiguous (network), the retry
   issued a SECOND close on the same ticket (duplicate/oversized close) or a close on an
   already-closed position.
3. `execute_market_order`: no retry (safe), but an ambiguous retcode was reported as failure —
   the manager layer then treats it as failed and may retry the entry on the next decision
   cycle, producing a duplicate position.

### Fix (standard idempotency pattern)
- Removed the duplicate `order_send` in `place_pending_order`.
- Added `_find_equivalent_pending(symbol, order_type, volume, price)` — before ANY retry of a
  pending order, scan `orders_get()` for an equivalent live order (same symbol/type/volume/
  price, magic 888101) and treat it as SUCCESS returning its ticket (never create a duplicate).
- `close_position` retry now re-checks `positions_get(ticket)` before re-sending: if the
  position is gone after an ambiguous retcode → the close succeeded, return True.
- `execute_market_order`: ambiguous-fill recovery — after a non-DONE retcode, verify a live
  position with (symbol, magic) appeared; if so return its ticket as success.

### Regression Guards
- `tests/unit/test_mt5_adapter.py`, `tests/unit/test_order_manager.py`,
  `tests/unit/test_order_lifecycle.py` — all pass post-fix.
- `ruff check` + `ruff format --check` clean.
- Manual verification pending live-broker smoke (read-only probe on MetaQuotes-Demo).

## BUG-063 — News Subsystem Disabled at Runtime (No news: Section in Any Config YAML)

- **Status**: FIXED (2026-08-17, news-UI forensics)
- **Severity**: HIGH (UI news panel permanently OFF / "News engine unavailable" despite a populated news.db)
- **Confidence**: HIGH (reproduced: `AppConfig.load_from_yaml` on every config file yields `news=None`; fixed config yields `news_enabled=True` end-to-end)
- **Verified**: full-stack probe — real `LiveEngine` + `TestClient`: `/api/news/state` 200 available=True state=STALE events=3; `/api/news` 200 with 5 real articles; `/api/news/health` 200 available=True; `/api/live/state` health.news=READY

### Root Cause
`AppConfig.news: NewsConfig | None = None` (configuration/config.py:110) with **no `news:` block present in `configs/base.yaml`, `configs/live.yaml`, or `configs/live.yaml.example`**. `load_from_yaml` does `cls(**raw_data)`, so the missing key leaves `news=None` → `live_engine.py:402` `_news_enabled = bool(config.news and config.news.enabled)` = **False** → `news_engine`/`news_worker` never constructed → every `/api/news*` route returns `{"available": False}` → UI badge OFF + "News engine unavailable" on Fetch. The DB (145 articles) was populated during an earlier session when news was enabled by other means; the subsystem was silently dead at runtime.

### Fix
Added a `news:` block (enabled: true, default safe params matching `NewsConfig`) to all three config files: `configs/base.yaml`, `configs/live.yaml`, `configs/live.yaml.example`.

### Regression Guards
- `AppConfig.load_from_yaml("configs/live.yaml").news is not None and .enabled is True`
- Real LiveEngine + TestClient: `/api/news/state` available=True; `/api/news` returns articles; health.news=READY
- Related fix (same session): `news/context.py` freshness clamp (see BUG-064)

## BUG-064 — News Context Validation Error Kills Whole Panel (freshness > 1.0)

- **Status**: FIXED (2026-08-17, news-UI forensics)
- **Severity**: HIGH (news context became unavailable → panel OFF/empty even when subsystem enabled)
- **Confidence**: HIGH (reproduced with real DB: fresh_sum=2.99 / weights=1.02 = 2.93 > 1.0 → Pydantic ValidationError)
- **Verified**: `tests/unit/test_news_phase12.py::TestLiveIntegration::test_67_context_freshness_clamped_when_weights_below_one` — fails with the exact ValidationError on the old formula, passes on the fix

### Root Cause
`news/context.py` computed `freshness = fresh_sum / weights` where `weights = Σ freshness×confidence×(0.5+relevance×0.5)` is typically < 1 with low-confidence analyses, while `fresh_sum` stays ~count-sized → ratio exceeded 1.0. `CurrentNewsContext.freshness` has Pydantic `le=1.0` (`news/models.py:358`) → every build raised → `engine.current_context()` caught and returned `available=False` safe defaults → `/api/news/state` → available=False → UI OFF + silent console.warn.

### Fix
`news/context.py`: `freshness = min(1.0, fresh_sum / article_count)` — correct average of per-article decay values, clamped like every other score in the dict. Added `count` accumulator.

### Regression Guards
- test_67: 8 low-confidence analyses (weights < 1) → context available=True, 0 ≤ freshness ≤ 1
- Real DB `current_context(force=True)` → available=True state=STALE freshness=0.085 (was available=False)

---

## BUG-065 — Seedable Built-in Strategy Engines (Ichimoku / Ichimili) Added (PHASE 15C)

- **Status**: FIXED (2026-08-17, PHASE 15C strategy seeding)
- **Severity**: FEATURE (new capability, not a defect)
- **Confidence**: HIGH (16 new unit tests: indicator math matches Pine reference, signal conditions, alternation/gap rules, deterministic candidates, idempotent seeder)
- **Verified**: `tests/unit/test_strategies_ichimili_phase15c.py` (12) + `tests/unit/test_strategies_seeder_phase15c.py` (4); ruff + mypy clean

### Motivation
The operator wanted the two Pine Script `Ichimili` strategies (the "Final Version" and the spaced-signal version) added as FIRST-CLASS strategies in the project: testable, seedable into the research pipeline, and ready for future AI-strategy alignment.

### What Was Added
1. **`src/nexus_scalp/strategies/base.py`** — pure `Strategy` protocol (no I/O, no order authority), `StrategySignal` dataclass, `donchian_mid()` helper, `make_candidate()` (deterministic content-addressed `StrategyCandidate`), `register_strategy()` / `builtin_candidates()`.
2. **`src/nexus_scalp/strategies/ichimoku.py`** — two engines translated line-by-line from the Pine reference:
   - `IchimiliFinalStrategy` (`STRAT-ICHIMILI-FINAL`): displaced-visible-Kumo body break + rising/falling future cloud + **alternating one-in-a-row** signal rule (Pine `lastSignalType`).
   - `IchimiliSpacedStrategy` (`STRAT-ICHIMILI-SPACED`): current-Kumo close break + span A/B momentum + **min-candles-between-signals** gap rule (Pine `lastSignalBar`).
   - Both expose `context_definition()`, `entry_logic()`, `exit_logic()`, `risk_assumptions()` for candidate seeding; import-time registration via `register_strategy`.
3. **`src/nexus_scalp/strategies/seeder.py`** — `seed_builtin_candidates()`: idempotent upserts into `strategy_registry` that PRESERVE existing backtest/walkforward/OOS/robustness/score results (registry immutability contract).
4. **`src/nexus_scalp/research/worker.py`** — `_refresh_once` now runs a `seed` step BEFORE dataset/discovery so the built-in candidates exist as registry entries every cycle (isolated, restart-safe, idempotent).

### Pine → Python Fidelity Notes
- Ichimoku math: `conversion = donchian(9)`, `base = donchian(26)`, `spanA = (conv+base)/2`, `spanB = donchian(52)`.
- Final variant evaluates the DISPLACED cloud (`leadLine1[displacement-1]`) exactly like the Pine signal block; the alternation emits BUY only when the last signal was not BUY (and vice versa).
- Spaced variant uses the current Kumo and enforces `bar_index - lastSignalBar >= minCandlesBetweenSignals`.

### Regression Guards
- `test_ichimoku_lines_match_pine` — donchian/tenkan/kijun/spanA/spanB values match hand-computed windows
- `test_final_variant_emits_buy_in_uptrend` + alternation assertion (no two identical directions consecutively)
- `test_spaced_variant_enforces_min_gap` — every consecutive signal ≥ min gap
- `test_candidates_deterministic_and_content_addressed` — version == canonical_version, lifecycle DISCOVERED
- `test_seed_is_idempotent_and_preserves_validation` — re-seeding never clobbers existing backtest results
- `test_worker_seeds_on_first_cycle` — ResearchWorker seed step persists both candidates
- Future strategies: implement the `Strategy` protocol, call `register_strategy()` at import, add tests → automatically discoverable via `builtin_candidates()` and seeded by the worker.

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

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

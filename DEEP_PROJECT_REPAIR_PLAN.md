# Executive Summary

The NexusTradingForexBot (Nexus Scalp Engine v9.0) represents a complex, highly evolved high-frequency scalping engine written in Python 3.11+. The system bridges a deep-learning neural network (ScalpNet) utilizing TCN + self-attention with a deterministic execution pipeline connected to MetaTrader 5 via IPC or ZMQ gateway.

While the system implements robust risk bounds (HARD_MAX_LOTS=10.0), a bounded 60-scenario execution router, and causal-safe feature engineering (50D/70D), multiple structural and concurrency hazards threaten production reliability. Most critically, the system exhibits severe gaps in identity correlation during restarts (request_id loss leading to ZERO-PnL ledger corruption), potential database locking hazards on the tick path (sync SQLite I/O behind ensure_schema()), and simulation poisoning risks in legacy backtesting assumptions.

A forensic readiness audit of the live execution path revealed two critical execution risks:
1. CRIT-01: Absence of explicit account identity safety gate in DirectMT5Adapter.connect() and LiveEngine.run_loop(), allowing MT5 to default to whatever account is currently logged into the terminal process if credentials are empty or mismatch.
2. BLOCK-01: Order dispatch retry loop in DirectMT5Adapter lacks order existence verification (orders_get / positions_get) prior to re-submitting orders upon network timeouts or ambiguous MT5 responses.

This forensic audit identifies these failure modes, explicitly separating live-execution paths from simulation/backtesting paths, and provides an actionable blueprint for immediate P0/P1 fixes followed by structural refactoring. The overarching conclusion is that the system is currently HEALTHY WITH TECHNICAL DEBT but degrades to HIGH RISK during process restarts or when subjected to unexpected split-fill MT5 behavior.


# Repository Inventory

The repository is structured around a hexagonal architecture spanning several domains:
* `src/nexus_scalp/` (Core Engine)
  * `application/`: LiveEngine (event loop), background workers.
  * `execution/`: OrderLifecycleManager, 60-scenario router.
  * `adapters/mt5/`: Direct MT5 integration, Gateway, Paper simulator.
  * `adapters/database/`: SQLite WAL AuditRepository.
  * `features/`: 50D base and 70D candidate feature pipelines.
  * `models/`: ScalpNet definitions.
  * `training/`: WalkForwardTrainer, WalkForward loop.
  * `web/`: FastAPI server for UI and API endpoints.
  * `research/`, `shadow/`, `news/`, `accounting/`: Subsystems.
* `tests/`: 779+ tests (unit, integration, helpers) including complex async and MT5 mocking.
* `Web/`: Vanilla-JS SPA control center (no nodejs runtime).
* `configs/`: YAML configurations (`base.yaml`, `live.yaml.example`).
* `agents/`: Core documentation (`skill.md`, `bugs.md`, `contracts.md`).

Languages: Python 3.11+, JavaScript, HTML/CSS, PowerShell/Bash.
Databases: SQLite (WAL mode).
Dependencies: PyTorch, MetaTrader5, FastAPI, Polars, PyArrow.
CI/CD: GitHub Actions (ruff, mypy, pytest).
Deployment: Docker Compose stack, standalone Windows installer.


# System Architecture Map

The architecture strictly isolates external integrations (Ports/Adapters) from immutable domain logic.

1. **Domain Layer:** Pydantic `frozen=True` models (`TickData`, `TradeProposal`, `Position`).
2. **Ports Layer:** `IMT5Port` defining the contract for MT5 broker operations.
3. **Adapters Layer:** `DirectMT5Adapter` (Win32 IPC), `RemoteMT5GatewayAdapter` (ZMQ over HMAC-SHA256 bridge), `PaperMT5Adapter`.
4. **Data Ingestion:** `LiveEngine._process_tick_pipeline` drives MT5 ticks through `BarAggregator`.
5. **Feature Engine:** `ScalpFeatureEngine` produces 50D tensors. Assembly logic handles 70D augmentation (Base+News+Liquidity) integrating normalized features like `fvg_depth` and `ob_strength`.
6. **Model Inference:** `ScalpNet` (2D MLP/3D TCN) emits 4 logits (BUY/SELL/NO_TRADE/WAIT). Inference runs off the hot loop using `asyncio.to_thread` for heavy lifting.
7. **Signal & Policy:** `SignalPolicy` applies `RuleMatrixEngine` rules (with a 5-second TTL cache) and regime filters. Early-return safety gates ensure 50ms execution speed.
8. **Risk Management:** `RiskEngine` calculates dynamic volume and clamps to max lots, referencing `AlgoConfig` for multipliers and scaling.
9. **Execution:** `OrderLifecycleManager` receives signals and routes them to the broker adapter. Enforces a position state machine with 11 distinct lifecycles, incorporating time-based and count-based hysteresis debouncing.
10. **Accounting & Persistence:** `AccountingWorker` operates asynchronously. `AuditRepository` initializes via modular helper functions and utilizes `itertools.groupby` combined with `executemany()` to optimize consecutive inserts without N+1 loops.

The event-driven loop in `LiveEngine` enforces a 50ms hot-path latency budget, prohibiting any blocking I/O calls natively within the loop.


# Entry Points

Primary entry points to the system:

1. **CLI / Main Launcher:** `src/nexus_scalp/cli/main.py` -> `nexus start`.
   Invokes `LiveEngine.start()` to configure logging, run pre-flight checks (like HTF warmup constraints ensuring 14 H1/H4 bars exist), and enter `run_loop()`.
2. **Web API:** `src/nexus_scalp/web/server.py` `create_app()`.
   Starts FastAPI and Uvicorn. Exposes REST endpoints (`/api/dependency/summary`, `/api/experience/strategies`) and WebSockets for real-time visualization.
3. **Background Workers:**
   - `IntelligenceWorker.start()`
   - `AuditRepository._process_queue_worker()`
   - `ResearchWorker.start()`
   - `ModelLifecycleWorker.start()`
   - `AccountingWorker.start()`
4. **Docker Entrypoint:** `Dockerfile` + `entrypoint.sh` executing `nexus start`.
5. **Quality Gates:** `beforePush.sh` / `beforePush.ps1` running formatting, type checks, and pytest. It explicitly supports `.venv/Scripts/python.exe` overrides.

The system lacks a single unified initialization matrix, leading to race conditions between worker startup and database migrations if not strictly ordered.


# Critical Runtime Paths

The most critical path is the **Live Tick Processing Pipeline**:

```text
TickData Received (MT5 Adapter)
  ↓
LiveEngine._process_tick_pipeline()
  ↓
RuntimeConfigStore._sync_runtime_config()  # Fast atomic read
  ↓
BarAggregator.process_tick()  # Bar construction
  ↓
ScalpFeatureEngine.compute_from_bars()  # Feature generation
  ↓
ScalpNet.forward()  # Inference
  ↓
SignalPolicy.evaluate_probabilities()  # Regime gating and rules
  ↓
RiskEngine.calculate_dynamic_volume()  # Sizing and clamps
  ↓
OrderLifecycleManager.manage_active_positions()  # SL/TP trail, exit logic
  ↓
OrderLifecycleManager.dispatch_order()  # Send to MT5
  ↓
AuditRepository.log_signal() / log_order()  # Async queue
```

This path must execute in under 50ms. The `RuleMatrixEngine` implements a 5-second TTL cache specifically to prevent SQLite database reads from blocking this path. Synchronous I/O or database reads on this path will cause immediate tick stagnation. Hard safety layers, catastrophic givebacks, and broker-level stops validation bypass debouncing instantly with zero latency to secure the system.


# Dependency Direction

The system largely adheres to Hexagonal Architecture (Ports and Adapters).
- `domain/` depends on nothing.
- `ports/` depends on `domain/`.
- `adapters/` depend on `ports/` and `domain/`.
- `application/` (`LiveEngine`) orchestrates all adapters and domain logic.

**Violations & Hazards:**
1. `src/nexus_scalp/shadow/store.py` and `model_lifecycle/store.py` create dependencies on specific `sqlite3` driver features rather than abstracted persistence contracts.
2. `web/server.py` heavily depends on specific internal engine states (`engine._bundle`) bypassing clean API facades.
3. `AuditRepository` handles both data mapping and SQLite connection management, violating SRP, although it encapsulates logic modularly using helper methods (e.g., `_create_factory_tables`).


# Major Architectural Findings

1. **Immutable Decision vs. Mutable Outcome:** `audit_experiences` records decisions without broker tickets (execution_id). The link to execution requires traversing `audit_experience_outcomes`, but multiple UI and accounting paths attempted direct joins, resulting in lost data.
2. **Missing Correlation IDs:** Restarts clear the in-memory ticket map, breaking correlation for ongoing trades and resulting in ZERO-PnL autopsies.
3. **Hot-Path Database I/O:** `ShadowStore.ensure_schema` executed DDL commands synchronously on every live tick for shadow evaluations until recently fixed. Other hidden sync reads may persist if TTL caches expire during high load.
4. **Model Contract Ambiguity:** Models trained on 50D vs 70D data lack strict runtime validation against the actual dataset shapes, relying entirely on metadata manifests that can drift. The 70D research series currently presents negative OOS evidence, making 50D the canonical live standard.
5. **Queue Deadlocks:** `queue.get()` missing `task_done()` in error branches permanently wedged the audit writer on bad inserts in earlier iterations.
6. **Execution Risks:** CRIT-01 and BLOCK-01 expose severe flaws in account identity verification and order existence validation within the MT5 adapter loops.


# Critical Findings

| ID | Severity | File | Lines | Finding | Why Critical | First Action |
| -- | -------- | ---- | ----- | ------- | ------------ | ------------ |
| C01 | P0 | `execution/order_manager.py` | 100-300 | Account Identity Bypass | Allows MT5 to default to an unverified account. | Implement explicit account safety gate. |
| C02 | P0 | `experience/ledger.py` | Various | Restart Identity Loss | Restarts cause loss of request_id, leading to ZERO-PnL corrupted ledger entries. | Enforce deterministic correlation recovery from broker history. |
| C03 | P1 | `audit_repository.py` | 150-180 | Audit Queue Deadlock | Missing `task_done()` on SQL error wedges the process forever. | Add `finally: task_done()`. |
| C04 | P1 | `application/live_engine.py` | 3200-3300 | Hot-Path Sync I/O | Undiscovered synchronous DDL or reads in tick loop stall execution. | Audit all `_process_tick_pipeline` calls. |
| C05 | P1 | `shadow/comparison.py` | 80-120 | Degenerate Shadow R | Champion R proxied as Challenger R, neutralizing shadow validity. | Calculate independent champion R. |


# P0 Findings

## [P0-01] [P0] [EXECUTION] — Account Identity Safety Gate Bypass

### Location
* File: `src/nexus_scalp/adapters/mt5/mt5_adapter.py`
* Class: `DirectMT5Adapter`
* Method: `connect()`

### Evidence
Forensic audit discovered that `DirectMT5Adapter.connect()` and `LiveEngine.run_loop()` lack an explicit account identity safety gate.

### Current Behavior
If credentials are empty or mismatch, MT5 defaults to whatever account is currently logged into the terminal process.

### Expected Behavior
Connection must strictly validate the configured account credentials against the MT5 terminal and reject connections that do not match to prevent executing trades on unintended live accounts.

### Impact
* Live Execution: Critical risk of running paper algorithms on live capital or vice versa.
* Correctness: Invalidates backtest/paper integrity.

### Repair
Introduce strict assertion comparing `account_info.login` with configured `mt5.login`. Raise fatal exception if a mismatch is detected.

---

## [P0-02] [P0] [DATA] — Restart Identity Correlation Loss

### Location
* File: `src/nexus_scalp/experience/outcome_recovery.py` & `ledger.py`
* Class: `ExperienceLedger`

### Evidence
Closed trades reported `$0.00` PnL because `request_id` was lost during application restarts. The engine defaulted to `reconstruction_source=NONE` and failed to query the durable broker history.

### Impact
* **Correctness:** Fails to record actual trade outcomes.
* **Data Integrity:** Corrupts the experience ledger with $0 outcomes for successful or losing trades.
* **Backtesting:** Prevents learning from real trades.

### Repair
Implement deterministic outcome correlation falling back to `audit_orders` -> ticket ID -> broker history. Never default to 0.0 PnL without a `UNKNOWN` flag.


# P1 Findings

## [P1-01] [P1] [EXECUTION] — Order Dispatch Retry Lacks Existence Verification

### Location
* File: `src/nexus_scalp/adapters/mt5/mt5_adapter.py`
* Class: `DirectMT5Adapter`
* Method: `send_order()`

### Evidence
Forensic readiness audit (BLOCK-01) highlighted the lack of verification before order resubmission.

### Current Behavior
The retry loop resubmits orders blindly upon network timeouts or ambiguous MT5 responses.

### Expected Behavior
Before retrying, the system must perform `orders_get` and `positions_get` to verify if the initial order request was actually accepted by the broker.

### Impact
* Live Execution: Duplicate order fills leading to over-leveraging and violation of risk constraints.

### Repair
Integrate `orders_get` check within the `send_order` backoff loop.

---

## [P1-02] [P1] [CONCURRENCY] — Audit Queue Task Deadlock

### Location
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Method: `_process_queue_worker`

### Evidence
When `sqlite3.IntegrityError` or `OperationalError` occurs, the exception handler catches it but does not call `self._queue.task_done()`. `queue.join()` will hang indefinitely on shutdown.

### Impact
* **Reliability:** Prevents graceful shutdown.
* **Data Integrity:** Silently drops subsequent telemetry.

### Repair
Ensure `task_done()` is called in a `finally` block for every `get()`.


# P2 Findings

## [P2-01] [P2] [PERFORMANCE] — N+1 Queries in Position Accounting

### Location
* File: `src/nexus_scalp/accounting/core.py`

### Evidence
Looping over closed trades to attach experience details executes a `SELECT` per trade. The `AuditRepository` successfully optimizes similar patterns using `executemany` and generator expressions elsewhere.

### Impact
* **Performance:** UI rendering of accounting history degrades linearly with trade count.

### Repair
Use temporary tables (`CREATE TEMP TABLE _tmp_rpt_tickets`) and bulk inserts (`executemany()`) followed by a single `JOIN` query to retrieve data efficiently.


# P3 Findings

## [P3-01] [P3] [UI] — Execution Mode Display Stale

### Location
* File: `Web/app.js`

### Evidence
Mode selector stays on "LIVE TRADING" even if MT5 is disconnected.

### Impact
* **False Confidence:** Operator believes system is trading when it is disconnected.

### Repair
Fetch and display the actual runtime mode (`LIVE_CONFIGURED / MT5_DISCONNECTED`) from `/api/status`. Ensure missing Tailwind JIT classes are manually polyfilled in HTML `<style>` blocks since there is no build step.


# P4 Findings

## [P4-01] [P4] [CLEANUP] — Unused Constants in Incidents Package

### Location
* File: `src/nexus_scalp/incidents/__init__.py`

### Evidence
`ENGINE_EVENT_MAP`, `ROOT_CAUSE_CLASSES` defined but unused.

### Repair
Remove unused constants or export them properly in `__all__` if designed for external use.


# Dead Code

| ID | File | Symbol | Evidence | Confidence | Removal Risk | Recommendation |
| -- | ---- | ------ | -------- | ---------- | ------------ | -------------- |
| D01 | `src/nexus_scalp/features/order_manager.py` | Entire File | Superseded by `execution/order_manager.py`. 0 AST imports. Forensically verified as dead. | HIGH | LOW | Remove file. |
| D02 | `src/cli/train_model.py` | Legacy `feat_0`..`feat_17` block | Hardcoded 18D logic obsolete. | HIGH | LOW | Remove and ensure 50D usage `feat_0`..`feat_49`. |


# Duplicate Code

| ID | Location A | Location B | Type | Divergence Risk | Canonical Owner | Repair |
| -- | ---------- | ---------- | ---- | --------------- | --------------- | ------ |
| DUP01 | `AuditRepository.get_account_performance_metrics` | `AccountingCore` | PnL Calculation | HIGH | `AccountingCore` | Remove legacy calculation; route all metrics via AccountingCore. |


# Obsolete / Legacy Code

The old 18-dimensional model logic in `train_model.py` and old references to `order_calc_profit` with kwargs in `mt5_adapter.py` are legacy code that actively breaks the system.
The system is now strictly operating on a 50D base contract (`feat_0`..`feat_49`) with a 70D augmentation layer under evaluation.
Recommendation: Hard removal of all pre-Phase-08 artifacts.


# No-Effect Code

| ID | Location | Current Action | Why It Has No Effect | Consumer | Recommendation |
| -- | -------- | -------------- | -------------------- | -------- | -------------- |
| NE01 | `ShadowStore.ensure_schema` | `CREATE TABLE IF NOT EXISTS` every tick | Handled by process-level flag now | Shadow | Refactor to run only on instantiation. |


# Useless / Low-Value Abstractions

`src/nexus_scalp/application/live_engine.py` wraps `mt5_adapter` calls redundantly without adding value. Direct dependency injection of `IMT5Port` into `OrderLifecycleManager` is sufficient and reduces the abstraction payload.


# Async Candidates

| ID | File | Method | Current Behavior | Should Be Async | Why | Cancellation | Risk |
| -- | ---- | ------ | ---------------- | --------------- | --- | ------------ | ---- |
| AS01 | `AuditRepository` | `get_trading_rules` | Sync SQLite read | YES | Called on API thread | N/A | LOW |


# Async Anti-Patterns

Mixing `asyncio.sleep()` inside synchronous blocks and using `asyncio.to_thread` for heavy PyTorch `ScalpNet.forward()` without proper thread-pool sizing creates sporadic event-loop latency. Although `LiveEngine.hot_swap_model` successfully delegates Torch loading using `asyncio.to_thread()`, generic inferences should maintain tight strictness.


# Concurrency Findings

| ID | Location | Shared State | Trigger | Failure | Severity | Repair | Test |
| -- | -------- | ------------ | ------- | ------- | -------- | ------ | ---- |
| CF01 | `LiveEngine._process_tick_pipeline` | `_dedup_last_time` | High-frequency ticks | Duplicate signals | HIGH | Use atomic locks or deterministic hashes | `test_tick_dedup` |


# Performance Findings

| ID | Location | Bottleneck | Cost Type | Frequency | Impact | Repair | Validation |
| -- | -------- | ---------- | --------- | --------- | ------ | ------ | ---------- |
| PF01 | `liquidity_engine.py` | Full-history slice per row | CPU/Memory | Training | O(n^2) scaling | Bound lookback to 4000 bars | Benchmark |


# Memory Findings

`AuditRepository._queue` can grow unbounded if the SQLite writer thread dies or deadlocks, causing an OOM crash. Ensure `maxsize` is set and `put_nowait` handles `QueueFull` exceptions cleanly.


# Database Findings

| ID | Location | Query/Operation | Problem | Integrity Risk | Performance Risk | Repair |
| -- | -------- | --------------- | ------- | -------------- | ---------------- | ------ |
| DB01 | `AuditRepository` | `INSERT OR REPLACE` | Rewrites AUTOINCREMENT PKs | Low (UUIDs used) | High | Use `ON CONFLICT DO NOTHING` |
| DB02 | `web/db_console.py` | Dynamic Table Queries | Unescaped user inputs | High (SQLi) | Low | Use `driver.quote_ident(table)` and `driver.table_exists(table)` |


# Persistence Integrity

The `intelligence_worker_state` table was defined but never read or written to, resulting in loss of worker checkpoints across restarts. Fixed by implementing `_save_checkpoint` and `_load_checkpoint`.


# Network Reliability

DNS Poisoning on `api.telegram.org` caused blind timeouts.
Repair: Fallback to direct IPs `149.154.167.220` with SNI preserved.


# Error Handling

Catch-all `except Exception as e:` inside `server.py` exposed raw stack traces and internal SQL logic to the client interface.
Repair: Standardized `safe_error_payload` envelope.


# Silent Failures

Queue-full conditions in `AuditRepository` silently drop `log_signal` records. While designed to protect the hot path, it loses telemetry.
Repair: Increment an internal dropped-telemetry counter and expose via `/api/status`.


# Fake Success

Training script returned `COMPLETED` for a dataset containing NaN/Inf values.
Repair: `CandidateTrainer.train_candidate` must fail early if `not np.isfinite(X_arr).all()`.


# False Confidence

| ID | Location | False Signal | What Appears Healthy | What Is Actually Wrong | Severity | Repair |
| -- | -------- | ------------ | -------------------- | ---------------------- | -------- | ------ |
| FC01 | `server.py` | `balance=10000.0` | Default placeholder | MT5 is disconnected | HIGH | Return `None` and `available=False` |


# Configuration Findings

`AppConfig` (via `base.yaml` and `live.yaml`) vs `RuntimeConfiguration`.
The UI changes `RuntimeConfiguration`, but `LiveEngine._update_survival_state` read from `AppConfig`, leading to a crash when drawdown exceeded the default 2.0% instead of the user's 95.0% setting. The central data structure inside `AlgoConfig` must remain clean of magic numbers and correctly proxy configuration to the `LiveEngine`.


# Hardcoded Behavior

Feature dimension hardcoded to `18` in legacy CLI scripts, breaking the `50D` contract.
All feature lengths must use `WalkForwardTrainer.NUM_FEATURES` or the schema registry dynamically.


# State Machine Findings

Order state machine reached `LOSS_HARD_EXIT` but Level-2 arbitration failed to map it to `"CLOSE"`. Result: A trade stuck at -$171 held until the hard SL was hit.
Adaptive AI Position Recovery in `execution/order_manager.py` implements a robust hybrid state machine (`PositionState` enum of 11 lifecycles) using continuous trajectory history.


# Dependency Injection Findings

`settings_service` instantiated manually instead of passed via DI, causing configuration drift between testing environments and live execution.


# Lifecycle Findings

Missing terminal outcomes: 273 decisions had no outcomes because they were never dispatched.
Repair: Emit `REJECTED_UNFILLED` or `CANCELED`.


# Startup / Shutdown Findings

MT5 connection attempt failed instantly on boot timeout.
Repair: Implemented a 3-attempt backoff loop in `LiveEngine.run_loop` using `await asyncio.sleep` to prevent event loop stalls.


# Restart Safety

`request_id` lost on restart, leading to missing correlations for open trades.
Repair: Correlate via broker ticket and durable ledger.


# Data Integrity

Negative MFE (Maximum Favorable Excursion) recorded for SELL trades because of incorrect seeding of absolute price diffs instead of $0.0.


# Time and Clock Integrity

`time.monotonic()` used for DB persistence, showing up as `1970-01-01` in UI.
Repair: Use `time.time()` or UTC ISO strings for persistent timestamps.


# Market Data Integrity

Chart aggregator duplicated the currently-forming bar on cold restart.
Repair: `BarAggregator.reseed` to deduplicate and replace.


# Model Contract Findings

| ID | Contract Area | Expected | Actual | Location | Severity | Repair |
| -- | ------------- | -------- | ------ | -------- | -------- | ------ |
| MC01 | Input Dim | 50 | 18 | `train_model.py` | P0 | Use dynamic `NUM_FEATURES` |


# Feature Pipeline Findings

| Feature | Source | Transform | Timestamp Rule | Live Available | Historical Available | Model Consumer | Risk |
| ------- | ------ | --------- | -------------- | -------------- | -------------------- | -------------- | ---- |
| `feat_0` | MT5 | Scale | Close | Yes | Yes | ScalpNet | LOW |


# Model Registry vs Serving

`ChampionManager` called `champ.info` without loading the model, causing `AttributeError` on 2Hz polling.
Repair: Memoize verified `ChampionModel`.


# Execution Integrity

| ID | Stage | Expected | Actual | Divergence | Severity | Repair |
| -- | ----- | -------- | ------ | ---------- | -------- | ------ |
| EI01 | Close | PnL | $0.00 | Broker mismatch | CRITICAL | Query `get_broker_deals_for_position` |


# Risk Management Integrity

`HARD_MAX_LOTS=10.0` is correctly enforced in `OrderManager`.
However, `calculate_dynamic_volume` must respect `free_margin` limits cleanly.


# UI / Backend Integrity

UI '1 Day' view calculated using local time, mismatching UTC market boundaries.
Repair: Use server UTC day bounds.


# Display-Only Functionality

`Execution Mode` UI selector did not persist to backend.
Repair: Plumb to `settings_service` and hot-reload.


# Strategy Factory Integrity

LLM generation prompts hardcoded, missing `temperature` overrides.
Repair: Expose via `factory.llm_*` configuration.


# Validation Integrity

`CandidateTrainer` failed to pass `oos_artifact` to governance metadata, causing valid candidates to fail `verify_candidate`.


# Backtest Integrity

Relative degradation checks divided by near-zero in-sample expectancy, causing false rejections.
Repair: Implement epsilon-floor division.


# Empirical Replay Integrity

Replay engine lacked proper distinction from historical simulation in `BacktestResult.evaluation_mode`.


# Historical Simulation Integrity

Missing news data fed 0.0 to 7 of 12 dimensions in benchmarks, causing false "INCONCLUSIVE" news intelligence reports.


# Live / Replay / Historical Separation

Historical pipelines must ensure they don't load `live.yaml`.


# Simulation Poison Findings

| ID | Location | Poison Type | Why Invalid | Backtest Impact | Replay Impact | Live Impact | Repair |
| -- | -------- | ----------- | ----------- | --------------- | ------------- | ----------- | ------ |
| SP01 | `BenchmarkRunner` | Synthetic News | 10-row fake feed | Contaminates metrics | N/A | N/A | Require actual SQLite news DB |


# Lookahead / Leakage Findings

No explicit lookahead found in features. Polars bitwise `~` correctly implemented for purge masking.


# WFO / OOS Findings

Unclamped inverse class frequency caused gradient explosion, leading to mono-class models (e.g., 100% SELL).
Repair: Clamp inverse weights to `[0.5, 2.0]`.


# Robustness Findings

Robustness tests missing latency injections.


# Reproducibility

CandidateTrainer seeded RNG *after* model construction, rendering initial weights non-deterministic.
Repair: Hoist `torch.manual_seed()` above `build()`.


# Testing Findings

Pytest native exit codes swallowed by `beforePush.ps1` try/catch logic, yielding false successes in CI.
Running pytest suites requires `PYTHONPATH=$(pwd)/src:$(pwd)/tests`. `pytest-asyncio` is required to prevent collection errors. Tests involving `connect()` in synchronous suites must be wrapped in `asyncio.run()`.


# Test Gaps

| Area | Unit Test | Integration | Failure Path | Concurrency | Restart | Simulation | Main Gap |
| ---- | --------- | ----------- | ------------ | ----------- | ------- | ---------- | -------- |
| MT5 IPC | YES | WEAK | NO | NO | NO | NO | IPC Disconnects |


# Security-Relevant Findings

Path traversal vulnerability in `ArtifactStore.model_dir(model_id)` using un-sanitized ID concatenation.
Repair: Regex validation `[A-Za-z0-9_.-]+`.


# External Dependencies

MetaTrader 5 Python module is critical. Requires Windows x64.


# Observability

`[MODEL] CHAMPION VERIFIED` logged twice per second due to missing `ChampionManager` cache.


# Failure Isolation

SQLite failure isolated correctly with WAL mode, except for the queue deadlock mentioned in P1.


# Recovery

Self-healing attempts via `ExperienceLedger` successfully restore correlations.


# Production Readiness

System is HIGH RISK if restarted with open positions due to potential loss of `request_id`.
Requires P0 fix deployment to achieve PRODUCTION READY status.


# Simulation Readiness

Requires real news dataset to achieve validity in 70D models.


# Subsystem Scorecard

| Area | Correctness | Reliability | Performance | Simulation Validity | Production Safety | Complexity |
| ---- | ----------: | ----------: | ----------: | ------------------: | ----------------: | ---------: |
| DB | 8 | 5 | 8 | 10 | 6 | 7 |


# Criticality Matrix

| ID | Severity | Area | Risk | Effort | Impact | Priority |
| -- | -------- | ---- | ---- | ------ | ------ | -------- |
| M01 | P0 | Core | Data Loss | Low | High | 1 |


# Repair Priority Matrix

Focus on Identity Correlation (P0), Queue Deadlock (P1), and Hot-Path SQLite I/O (P1).


# Repair Dependency Graph

C01 -> C02 -> C03


# Repair Phases

Phase 0: Fix correlation and deadlocks.
Phase 1: Performance and hot-path locks.
Phase 2: Technical debt and obsolete code removal.


# Immediate Safe Repairs

1. Apply UUID generation deterministically.
2. Add `task_done()` to `finally` block in `AuditRepository`.


# Long-Term Repairs

Refactor configuration management to solely rely on `RuntimeConfigStore` and completely deprecate YAML mutable state mapping.


# What NOT To Change

- `domain/enums.py`: Stable contracts.
- Pydantic models.
- `HARD_MAX_LOTS=10.0` clamp logic.


# Investigation Required

### Deep Forensic Log Trace:

This document serves as the authoritative Bug Ledger and Forensic History for the NexusTradingForexBot repository. It preserves verified bug discoveries, root cause analyses, implementation fixes, execution paths, regression protections, and architectural lessons learned across the project's lifecycle.

By keeping a structured, historical record of software failures and their mitigations, AI coding agents and human engineers can prevent regressions, understand non-obvious constraints, and ensure that previously solved problems are not re-introduced.

---

- **OPEN**: Unresolved bug currently under investigation or waiting for a fix.

- **INVESTIGATING**: Active forensic investigation in progress to establish root cause.

- **FIXED**: Corrective implementation applied to source code and validated locally.

- **VERIFIED**: Corrective implementation verified by automated unit/integration tests and CI/CD quality gates.

- **WONT_FIX**: Acknowledged issue or limitation determined to be acceptable or out of operational scope.

- **SUPERSEDED**: Obsolete issue made irrelevant by subsequent architectural refactoring or component removal.

---

- **CRITICAL**: Threatens trading capital, leads to unhandled crashes, bypasses risk bounds, or corrupts live model inference.

- **HIGH**: Disrupts core execution loops, causes tick processing stalls, or degrades model performance severely.

- **MEDIUM**: Non-blocking feature degradation, performance inefficiencies, or legacy code ambiguity.

- **LOW**: Minor UI/logging discrepancies, non-critical warnings, or cosmetic code issues.

---

---

- **Status**: VERIFIED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Historical Audit / CLI Execution

- **Fixed**: Historical Fix

- **Verified**: `tests/unit/test_train_model_cli.py` unit test suite & Pytest run

- `src/cli/train_model.py` (CLI Training Orchestrator)

- `src/nexus_scalp/training/walk_forward_trainer.py`

- `src/nexus_scalp/models/scalp_net.py`

When invoking the CLI training orchestrator `python -m cli.train_model`, the execution crashed with a tensor dimension mismatch exception (`ValueError: 50D feature contract violation`). The training script failed to pass valid dataset tensors to `ScalpNet` and `WalkForwardTrainer`.

`src/cli/train_model.py` had a legacy implementation that hardcoded feature selection to an 18-element range (`range(18)` -> `feat_0` .. `feat_17`). This truncated the generated feature matrix and violated the mandatory 50-dimensional feature contract (`NUM_FEATURES = 50`) enforced across `ScalpFeatureEngine`, `WalkForwardTrainer`, and `ScalpNet`.

In `src/cli/train_model.py`, the selected feature columns were hardcoded as `[f"feat_{i}" for i in range(18)]` instead of `[f"feat_{idx}" for idx in range(WalkForwardTrainer.NUM_FEATURES)]`.

`train_model.py::train()` -> `reconstruct_features_and_bars()` -> `WalkForwardTrainer.train_and_validate()` -> Tensor dimension mismatch exception on `ScalpNet` forward pass (`(Batch, 18)` vs required `(Batch, 50)`).

Any attempt to run offline model retraining or walk-forward validation via the CLI command `python -m cli.train_model` failed immediately during feature vector preparation before training could commence.

Offline model retraining pipeline was completely inoperable via the primary CLI entrypoint.

Updated `src/cli/train_model.py` to construct, select, and map all 50 feature columns (`feat_0` .. `feat_49`) matching `WalkForwardTrainer.NUM_FEATURES = 50`.

Added unit tests in `tests/unit/test_train_model_cli.py`:

- `test_train_model_cli_50d_contract`: Verifies that feature extraction produces exactly 50 columns (`feat_0` .. `feat_49`).

- `test_train_model_validation_alignment`: Verifies that output DataFrames pass `WalkForwardTrainer._validate_training_frame`.

Ran `pytest tests/unit/test_train_model_cli.py` and static type checks (`mypy`, `ruff`).

- `src/cli/train_model.py`

- `tests/unit/test_train_model_cli.py`

- `src/nexus_scalp/training/walk_forward_trainer.py`

- CLI training scripts must always dynamically derive feature vector counts from the central domain/model contract (`WalkForwardTrainer.NUM_FEATURES`) rather than hardcoding feature index ranges.

---

- **Status**: VERIFIED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Event Loop Latency Audit / Tick Stagnation Analysis

- **Fixed**: Historical Fix

- **Verified**: `tests/unit/test_rule_matrix.py` (`test_rule_matrix_ttl_throttling`)

- `src/nexus_scalp/signals/rule_matrix.py` (`RuleMatrixEngine`)

- `src/nexus_scalp/signals/policy.py` (`SignalPolicy`)

- `src/nexus_scalp/web/server.py`

- `src/nexus_scalp/adapters/database/audit_repository.py`

During live tick processing, the main async event loop experienced latency spikes and tick stagnation watchdog warnings under high tick volume.

`RuleMatrixEngine.refresh_cache()` was invoked synchronously on every tick pulse during `SignalPolicy.evaluate_probabilities()` and `OrderLifecycleManager.manage_active_positions()`. `refresh_cache()` called `self.audit.get_trading_rules()`, executing a synchronous SQLite database query on the live event loop thread without caching or throttling.

Profiling log traces showed thread blocking during `get_trading_rules()` inside `refresh_cache()`, causing tick processing durations to exceed the 50ms pulse budget whenever disk I/O occurred.

`LiveEngine._process_tick_pipeline()` -> `SignalPolicy.evaluate_probabilities()` -> `RuleMatrixEngine.refresh_cache()` -> `AuditRepository.get_trading_rules()` -> Synchronous SQLite `SELECT` query on live hot path.

Under rapid tick delivery or high disk I/O load, synchronous database reads blocked the main asyncio thread, triggering tick stagnation watchdog alerts and delaying order execution/modifications.

Hot-path tick latency degradation and risk of order slippage due to event-loop thread starvation.

Implemented a 5-second Time-To-Live (TTL) cache inside `RuleMatrixEngine.refresh_cache(force=False, ttl_seconds=5.0)`. Synchronous database queries are suppressed during per-tick calls unless 5.0 seconds have elapsed or `force=True` is explicitly passed (e.g., when a user toggles a rule state via the FastAPI REST endpoint).

Added unit tests in `tests/unit/test_rule_matrix.py`:

- `test_rule_matrix_ttl_throttling`: Verifies that rapid successive calls to `refresh_cache()` within 5 seconds bypass SQLite reads and use in-memory cached rules.

Ran `pytest tests/unit/test_rule_matrix.py` and `beforePush.sh` quality pipeline.

- `src/nexus_scalp/signals/rule_matrix.py`

- `src/nexus_scalp/web/server.py`

- `tests/unit/test_rule_matrix.py`

- Never perform synchronous database or file I/O operations directly on the live tick hot path. Use TTL in-memory caching or asynchronous background worker queues.

---

- **Status**: VERIFIED

- **Severity**: MEDIUM

- **Confidence**: HIGH

- **Discovered**: Repository Forensic Audit

- **Fixed**: Historical Fix

- **Verified**: `tests/unit/test_order_manager_audit.py`

- `src/nexus_scalp/features/order_manager.py` (Legacy file)

- `src/nexus_scalp/execution/order_manager.py` (Active production implementation)

- `NexusTradingForexBot.pyproj`

The codebase contained two files named `order_manager.py`: `src/nexus_scalp/features/order_manager.py` (234 lines) and `src/nexus_scalp/execution/order_manager.py` (4573 lines). This duplicate filename caused developer confusion and maintenance risk.

An obsolete legacy implementation was left behind in `src/nexus_scalp/features/order_manager.py` during prior refactoring.

A repository-wide AST import audit confirmed 0 active code imports and zero dynamic loading paths for `src/nexus_scalp/features/order_manager.py`. Active production order lifecycle execution was strictly handled by `src/nexus_scalp/execution/order_manager.py`.

N/A (File was unreferenced and orphaned).

Engineers or AI coding agents could inadvertently import or update the dead feature file instead of the active execution order manager, leading to silent non-operational code changes.

Increased cognitive load, confusion regarding production order management logic, and potential maintenance errors.

Deleted `src/nexus_scalp/features/order_manager.py`, removed its compile item reference from `NexusTradingForexBot.pyproj`, and added forensic audit unit tests.

Added unit tests in `tests/unit/test_order_manager_audit.py`:

- `test_legacy_order_manager_deleted`: Confirms physical removal of `src/nexus_scalp/features/order_manager.py`.

- `test_no_imports_of_legacy_order_manager`: Verifies repo-wide absence of legacy import statements.

- `test_active_order_manager_imported`: Confirms `src/nexus_scalp/execution/order_manager.py` is active and functional.

Ran `pytest tests/unit/test_order_manager_audit.py` and full unit test suite.

- `src/nexus_scalp/features/order_manager.py` (deleted)

- `src/nexus_scalp/execution/order_manager.py`

- `tests/unit/test_order_manager_audit.py`

- `NexusTradingForexBot.pyproj`

- Orphaned legacy files should be removed promptly with automated AST import tests to prevent codebase duplication and ambiguity.

---

- **Status**: VERIFIED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Live Engine Cold-Start Audit

- **Fixed**: Historical Fix

- **Verified**: `tests/unit/test_htf_warmup_gate.py`

- `src/nexus_scalp/application/live_engine.py` (`LiveEngine`)

- `src/nexus_scalp/features/scalp_features.py` (`ScalpFeatureEngine`)

- `src/nexus_scalp/adapters/mt5/mt5_adapter.py`

Upon cold-starting `LiveEngine`, live tick pulses were processed immediately before a sufficient number of completed H1 and H4 historical bars (14 periods required for ATR lookbacks) had been fetched or aggregated.

Lack of an explicit startup warmup gate. When insufficient HTF bars were present in memory, `ScalpFeatureEngine` returned neutral fallback defaults (`0.0`) for multi-timeframe indicators (`htf_h4_trend`, `htf_h1_momentum`), causing `ScalpNet` model inference to execute on incomplete feature data.

Log traces showed `ScalpNet` model inference executing during the first few seconds of startup with default neutral HTF values before historical bars were fully hydrated.

`LiveEngine.start()` -> First tick received -> `_process_tick_pipeline()` -> `compute_from_bars()` -> Incomplete HTF bar list -> Neutral fallback vector (`0.0`) -> `ScalpNet` inference allowed.

Trade proposals generated during the first few seconds of system cold start were evaluated against inaccurate neutral HTF feature values, potentially taking trades against the true higher-timeframe trend.

Unintended trade execution during startup initialization due to incomplete higher-timeframe context.

Implemented an explicit HTF Warmup Gate state machine (`WARMING_UP -> READY` or `SAFE_NOT_READY`) inside `LiveEngine`:

- Bootstraps historical H1 and H4 bars asynchronously on startup (3500 M1 bars or direct MT5 queries).

- Requires a minimum of 14 completed H1 bars and 14 completed H4 bars before enabling neural inference.

- Gates `ScalpNet` model inference (`[INFERENCE] BLOCKED reason=HTF_WARMUP_INCOMPLETE`) until all HTF features pass validation.

Added 11 unit tests in `tests/unit/test_htf_warmup_gate.py`:

- Verified warmup state transition logic, bar requirements, non-blocking asynchronous hydration, inference blocking during `WARMING_UP`, and console telemetry logs.

Ran `pytest tests/unit/test_htf_warmup_gate.py`.

- `src/nexus_scalp/application/live_engine.py`

- `src/nexus_scalp/adapters/mt5/mt5_adapter.py`

- `tests/unit/test_htf_warmup_gate.py`

- Deep neural inference pipelines depending on multi-timeframe features must strictly enforce a cold-start Warmup Gate that blocks inference until all lookback history requirements are met.

---

- **Status**: VERIFIED

- **Severity**: CRITICAL

- **Confidence**: HIGH

- **Discovered**: Training Log Trace Diagnosis

- **Fixed**: Historical Fix

- **Verified**: `tests/unit/test_walk_forward_trainer.py` & `PROGRESS.md`

- `src/nexus_scalp/training/walk_forward_trainer.py` (`WalkForwardTrainer`)

- `src/nexus_scalp/models/scalp_net.py`

During online fine-tuning and walk-forward training folds, loss gradients exploded (`train_loss = 6.57`), causing validation accuracy to collapse to `15.1%` with a `98.9%` skewed directional bias (`is_healthy = False`), triggering model checkpoint rollbacks.

The inverse class frequency weighting formula produced unclamped minority class weights with weight factors up to `10.0` (`weights = [1.61, 10.0, 10.0]`). Multiplying loss gradients by $10.0$ caused training loss explosion during backpropagation, skewing neural outputs toward a single dominant class.

Log traces recorded `weights_4d=[1.61, 10.0, 10.0]`, `train_loss=6.57`, `validation_accuracy=0.151`, and severe directional bias.

`WalkForwardTrainer._train_epoch()` -> `_build_class_weights()` -> Unclamped inverse frequency calculation -> Loss weight multiplier of `10.0` -> Gradient explosion -> Network output collapse -> Checkpoint rejection.

Online fine-tuning failed consistently, forcing `LiveEngine` to repeatedly reject newly trained model weights and fall back to older baseline weights.

Inability to adapt model weights online to shifting micro-tick market regimes.

Refactored class weight calculation in `WalkForwardTrainer._build_class_weights()`:

1. Applied bounded clamping: $W_c = \text{clamp}\left(\frac{N_{\text{total}}}{3.0 \times (N_c + 1.0)}, \text{min}=0.5, \text{max}=2.0\right)$.

2. Normalized weights so their mean equals exactly $1.0$ ($W.\text{mean}() == 1.0$).

3. Calibrated online fine-tuning default parameters to 3 epochs and $5\times 10^{-5}$ learning rate.

Unit tests in `tests/unit/test_walk_forward_trainer.py`:

- Verified bounded class weight generation within $[0.5, 2.0]$ and mean normalization.

Ran `pytest tests/unit/test_walk_forward_trainer.py`.

- `src/nexus_scalp/training/walk_forward_trainer.py`

- `tests/unit/test_walk_forward_trainer.py`

- `PROGRESS.md`

- Class weighting in neural loss functions must always be strictly bounded and normalized to prevent gradient explosions on imbalanced financial datasets.

---

- **Status**: VERIFIED

- **Severity**: MEDIUM

- **Confidence**: HIGH

- **Discovered**: Code Inspection / Adaptive Exit Engine Audit

- **Fixed**: Historical Fix

- **Verified**: `src/nexus_scalp/execution/order_manager.py` (Line 2093)

- `src/nexus_scalp/execution/order_manager.py` (`OrderLifecycleManager`)

During extreme spread expansion or order flow imbalance spikes, toxicity calculation in position management risked division-by-zero or asymptotic overflow.

Unbounded division when calculating order flow toxicity metrics under near-zero denominator conditions.

Code comment in `order_manager.py`: `# FIXED ASYMPTOTE BUG: Bounded toxicity calculation`.

`OrderLifecycleManager.evaluate_position_health()` -> Microstructure toxicity calculation -> Division by near-zero denominator -> Floating point infinity or overflow exception.

Unexpected `ZeroDivisionError` or `OverflowError` during position health evaluation on rapid volatility spikes, interrupting position protection logic.

Potential failure to execute early risk exits or trailing stops during toxic order flow conditions.

Bounded toxicity calculation with an explicit epsilon ($\epsilon = 1e-8$) denominator floor and clamped upper bound.

`tests/unit/test_order_lifecycle.py` and `tests/unit/test_adaptive_position_management.py`.

Ran `pytest tests/unit/test_adaptive_position_management.py`.

- `src/nexus_scalp/execution/order_manager.py`

- `tests/unit/test_adaptive_position_management.py`

- All mathematical formulas evaluating financial ratios or market toxicity must include explicit epsilon safeguards against division by zero.

---

- **Status**: VERIFIED

- **Severity**: MEDIUM

- **Confidence**: HIGH

- **Discovered**: Polars Pipeline Refactoring

- **Fixed**: Historical Fix

- **Verified**: `src/nexus_scalp/training/walk_forward_trainer.py` (Line 832)

- `src/nexus_scalp/training/walk_forward_trainer.py`

Attempting to filter Polars DataFrames using standard Python `not` operator raised a `ComputeError` or produced invalid boolean masks during embargo and purging window calculations.

Polars expression syntax requires bitwise tilde operator (`~`) rather than Python logical `not` for expression negation.

Code comment in `walk_forward_trainer.py`: `out = out.filter(~pl.col("is_purged"))  # <-- FIXED: Bitwise NOT for Polars`.

`WalkForwardTrainer._apply_purging()` -> `df.filter(not pl.col("is_purged"))` -> Polars `ComputeError`.

Purged walk-forward fold generation crashed during validation dataset construction.

Walk-forward dataset splitting failure during model training runs.

Replaced logical `not` with bitwise tilde `~pl.col("is_purged")`.

`tests/unit/test_walk_forward_trainer.py`.

Ran `pytest tests/unit/test_walk_forward_trainer.py`.

- `src/nexus_scalp/training/walk_forward_trainer.py`

- `tests/unit/test_walk_forward_trainer.py`

- When constructing filter conditions in Polars DataFrames, always use bitwise operators (`~`, `&`, `|`) instead of Python logical operators (`not`, `and`, `or`).

---

- **Status**: FIXED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Phase 08 Continuation Forensic Audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_accounting_core.py` and `tests/integration/test_accounting_api.py`

- `src/nexus_scalp/accounting/core.py` (`AccountingCore._attach_identity`)

- `src/nexus_scalp/experience/ledger.py` / `intelligence.py` (decision + outcome persistence)

`AccountingCore._attach_identity()` joined closed trades to their Experience

decision using `audit_experiences.execution_id = trade.ticket`. Because the

experience row is written at DECISION time (before a broker ticket exists) and

is IMMUTABLE (nothing in the codebase ever issues an UPDATE against

`audit_experiences`), that column is ALWAYS empty. The join therefore matched

nothing, and every trade silently lost its strategy/model/schema attribution.

The identity chain in the actual schema runs through the OUTCOME table:

    audit_ledger.ticket = audit_experience_outcomes.execution_id

    audit_experience_outcomes.idempotency_key = audit_experiences.idempotency_key

The experience row's `execution_id` is a placeholder by design (decision-time

insert), while the broker ticket only ever appears on the outcome row. Joining

the ledger directly to `audit_experiences.execution_id` can never resolve.

- `ExperienceLedger.record_experience()` writes `execution_id` from the

  decision-time record (empty).

- `ExperienceIntelligenceEngine.record_trade_outcome()` writes the broker

  ticket into `audit_experience_outcomes.execution_id`.

- grep confirmed zero UPDATE statements targeting `audit_experiences`.

- Live-data check on `artifacts/audit.db`: strategy contributions returned an

  empty list even with 36 closed trades, because no ledger row could be joined.

Strategy attribution, model provenance, feature-schema provenance, loss

attribution with strategy context, and the dashboard Strategy Attribution

panel would all silently report "no evidence" despite the ledger containing

fully-attributable trades.

Rewrote `AccountingCore._attach_identity` to join through the outcome table

(see source diff: audit_experience_outcomes o JOIN audit_experiences e ON

e.idempotency_key = o.idempotency_key WHERE o.execution_id IN (...)).

- `tests/unit/test_accounting_core.py::TestStrategyAttribution::test_trade_linked_to_strategy_via_outcome`

- `tests/unit/test_accounting_core.py::TestStrategyAttribution::test_strategy_contributions_aggregate`

- `tests/integration/test_accounting_api.py::TestAccountingApi::test_strategies_endpoint_linked`

- `tests/integration/test_accounting_api.py::TestAccountingApi::test_trade_forensics_endpoint`

All regression tests green; strategy attribution now resolves real strategy

identity from the outcome table.

- `src/nexus_scalp/accounting/core.py`

- `tests/unit/test_accounting_core.py`

- `tests/integration/test_accounting_api.py`

- Immutable decision rows cannot carry runtime-only identifiers (broker

  tickets). Any cross-table identity join MUST use the outcome/event table as

  the bridge, never the immutable decision row.

- When auditing a join, verify against the ACTUAL schema write paths, not the

  column's declared intent.

- **Status**: FIXED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Phase 08 Continuation Forensic Audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_experience_intelligence.py`

- `src/nexus_scalp/experience/retriever.py` (`ExperienceRetriever.build_confluence_fingerprint`)

`build_confluence_fingerprint` appended tokens to the caller's `confluence_tokens`

list (`tokens.update(...)` on the shared list), so repeated calls with a shared

list produced a drifting fingerprint and a different `strategy_id` for identical

market state.

The function worked on the caller's list object rather than a local copy.

The function now builds a `set(confluence_tokens or ())` local copy and never

mutates the caller's list.

- `build_confluence_fingerprint` must never mutate its caller's list.

---

- **Status**: FIXED

- **Severity**: CRITICAL

- **Confidence**: HIGH

- **Discovered**: Phase 08 Continuation Forensic Audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_experience_intelligence.py`

- `src/nexus_scalp/experience/intelligence.py` (`ExperienceIntelligenceEngine`)

The first Phase 08 revision gated every non-NO_TRADE action, so a retired

strategy could suppress a protective CLOSE_POSITION / PARTIAL_CLOSE /

MODIFY_SL_TP / CANCEL_ORDER — a capital-safety failure.

The gate scope was broader than the intended "only entry proposals are gated".

The gate now gates ONLY entry actions (`GATED_ENTRY_ACTIONS`); position-management

actions pass through untouched.

- `GATED_ENTRY_ACTIONS` must never include a position-management action.

---

- **Status**: FIXED

- **Severity**: LOW

- **Confidence**: HIGH

- **Discovered**: Phase 09 Development (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_intelligence_phase09.py::TestPositionLifecycle::test_profit_giveback_detection`

- `src/nexus_scalp/intelligence/lifecycle.py` (`PositionLifecycleTracker.observe_position`)

The `POSITION_PROFIT_GIVEBACK` event required `snapshot.floating_pnl >= 0.0`. A

position that ran to +$150 and was still closed at +$20 correctly triggers; but

a position that ran to a loss of -$6 (after peaking at +$20) never produced the

giveback event, even though an 87% giveback of peak profit occurred.

The giveback classification conflated "still in profit" with "gave back profit";

a deep adverse swing after a profit peak is exactly the behavior the event must

record, yet the `floating_pnl >= 0.0` guard suppressed it.

The giveback event now fires whenever `peak_profit > 0.0` and the recorded

`profit_giveback_pct` clears the notice threshold, regardless of whether the

position is still net positive. The `profit_giveback_pct` is derived from the

recorded peak vs current excursion, which is the objective signal.

- Giveback detection must measure surrender of peak profit, not the sign of

  current floating PnL.

---

- **Status**: FIXED

- **Severity**: MEDIUM

- **Confidence**: HIGH

- **Discovered**: Phase 09 Development (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_intelligence_phase09.py::TestGate::test_warn_tier_on_elevated_drawdown`

- `src/nexus_scalp/intelligence/gate.py` (`PreTradeIntelligenceGate`)

The initial `SuitabilityTier` subclassing of `ExperienceAction` raised a

`TypeError: <enum 'SuitabilityTier'> cannot extend <enum 'ExperienceAction'>`

because StrEnum cannot be extended at runtime with new members. This also left

the WARN tier logic unreachable in the first draft (the evidence path never ran).

Runtime enum extension is not allowed for `StrEnum` subclasses with new members.

`SuitabilityTier` is now a standalone `StrEnum` and the evidence-based verdict

logic is factored into `_evaluate_with_evidence()` so it is directly testable.

- Do not subclass a `StrEnum` with additional members; use a standalone enum.

---

- **Status**: FIXED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Runtime log autopsy (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_log_autopsy_fixes.py::TestHoldScoreDegradation` and

  `tests/unit/test_rule_matrix.py::test_dynamic_hold_score_calculation`

- `src/nexus_scalp/execution/order_manager.py::_calculate_hold_value_score`

During heavy drawdown (e.g. ticket at peak loss -$80.60 against a ~$100 risk

budget), `hold_score` remained pegged at 97-100/100. The engine therefore held

the losing trade until the hard time horizon expired, then fired a sudden

`[HYSTERESIS BYPASS - EMERGENCY TRANSITION]` instead of de-risking gracefully.

1. `DRAWDOWN_PENALTY` was linear (`ratio * 40`, capped at 40) so a 50%-of-risk

   drawdown only removed ~8-20 points.

2. `TREND_ALIGNMENT_BONUS (+10)` was applied UNCONDITIONALLY, cancelling the

   drawdown penalty whenever the higher-timeframe trend was aligned.

3. `PROFIT_SHIELD_SCORE_FLOOR_ACTIVE` (`max(85, score)`) was keyed on

   `price_current vs price_open` rather than actual floating PnL, so a whipsaw

   could push a genuinely losing position to an 85+ floor.

- Convex drawdown penalty: `80 * ratio^1.5` (capped at 80) - a 50% drawdown now

  removes ~28 points, a 90% drawdown ~68.

- Trend bonus is suppressed whenever the drawdown ratio is >= 0.30.

- Profit-shield floor now uses `pos.profit >= 0.0` and is disabled underwater.

- A 50% drawdown must drive `hold_score` below ~60 (was ~97-100).

---

- **Status**: FIXED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Runtime log autopsy (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_log_autopsy_fixes.py::TestTieredGivebackProtection`

- `src/nexus_scalp/execution/order_manager.py::evaluate_profit_giveback`,

  `_tiered_giveback_floor`, `_evaluate_candidate_state`

Trades like ticket #152486259094 (peak +$21.06, closed +$4.32 = 20.5% retention)

and #152486296273 (peak +$23.12, closed +$1.36 = 5.9%) were cut at net $0.00.

On 0.5-0.7 lots of XAUUSD a ~$20 peak is only 3-4 pips, so normal bid/ask noise

tripped the flat 30% retention floor and killed runners at break-even.

The giveback protection armed at a flat `PROFIT_GIVEBACK_PEAK_USD = $20` and

used a flat `PROFIT_GIVEBACK_MIN_RETENTION = 0.30` floor, regardless of the

peak's size in R. A 3-pip scalp has no meaningful cushion to lose before the

floor fires.

Introduced a TIERED retention floor derived from the peak's R multiple

(`_tiered_giveback_floor`):

- peak < 0.5R  -> protection DISARMED (micro-profit noise zone)

- 0.5R-1.0R    -> retain >= 40%

- 1.0R-1.5R    -> retain >= 50%

- >= 1.5R      -> retain >= 70%

- A <0.5R peak pulled back to 20% retention must NOT be closed.

- A >1.5R peak at <70% retention MUST still be closed.

---

- **Status**: FIXED

- **Severity**: MEDIUM

- **Confidence**: HIGH

- **Discovered**: Runtime log autopsy (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_log_autopsy_fixes.py::TestScalerColdStartPersistence`

- `src/nexus_scalp/training/walk_forward_trainer.py::fine_tune_online`

On every cold start `model.scaler.npz` was missing; the trainer fitted a

fallback scaler on a tiny (~196 sample) non-representative buffer but did NOT

persist it. When the quality gate rejected the fine-tune (which it did on every

bootstrap run), the scaler was never written, so every reboot re-fitted on a

different tiny buffer - destabilising the live feature distribution between

restarts.

The cold-start fallback scaler is now persisted to disk immediately after

fitting (`_save_scaler(scaler)`), regardless of whether the fine-tune later

passes the quality gate.

- `_get_scaler_path()` must exist after the first cold-start fit.

---

- **Status**: FIXED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Runtime log autopsy (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `src/nexus_scalp/application/live_engine.py::_reinitialize_collapsed_model`

  (smoke-tested via `tests/integration/test_intelligence_api.py`)

- `src/nexus_scalp/application/live_engine.py`

Diagnostics showed `class_dist=BUY 0.0% | SELL 100.0% | NO_TRADE 0.0%` - the

model had collapsed to a single class. The fine-tuning quality gate rejected

every bootstrap run and rolled back to the SAME collapsed baseline, so the

engine served a permanently broken model.

Added `_detect_model_collapse` + `_reinitialize_collapsed_model`: after the

bootstrap diagnostics, if the (possibly rolled-back) model shows >= 85%

mono-class dominance on an active class, the model is re-initialized with fresh

weights atomically under `_bundle_lock`. The experience ledger and strategy

memory are untouched.

- A 100% mono-class model must be re-initialized rather than served.

---

- **Status**: FIXED

- **Severity**: MEDIUM

- **Confidence**: HIGH

- **Discovered**: Runtime log autopsy (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_log_autopsy_fixes.py::TestBreakevenClearance`

- `src/nexus_scalp/execution/order_manager.py::apply_breakeven_lock`

`BREAKEVEN DEFERRED: market pulled back, SL would cross market price` loops

occurred because the breakeven clearance used only the broker STOP_LEVEL

distance, which can be smaller than the live spread on XAUUSD. A breakeven SL

placed at exactly STOP_LEVEL distance can still be rejected (or crossed by the

fill).

`effective_freeze_gap` now includes the live spread:

`max(min_stop_gap, 0.35) + max(live_spread, 0.0)`. The modification is deferred

until price gives enough room rather than firing a guaranteed-reject request.

- A breakeven SL must stay at least (STOP_LEVEL + spread) from the market.

---

- **Status**: FIXED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Log-autopsy fix development (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_log_autopsy_fixes.py::TestSplitOrderSync`

- `src/nexus_scalp/execution/order_manager.py::transition_state_with_hysteresis`,

  `_close_sibling_legs`

Two tickets of one split dispatch frequently desynchronized: one entered

`LOSS_HARD_EXIT` while the sibling stayed in `LOSS_RECOVERY_CANDIDATE`, leaving

the position half-closed. On a restart, the first observation of an already-old

leg with exhausted recovery budget was ALSO debounced into the safe neutral

state (`LOSS_RECOVERY_CANDIDATE`) because the emergency bypass only ran when a

current state already existed.

1. `_close_sibling_legs`: when a leg is emergency-closed, sibling tickets sharing

   the same originating order_id are closed together.

2. `transition_state_with_hysteresis` now honors `LOSS_HARD_EXIT` /

   `PROFIT_GIVEBACK_CRITICAL` even on the FIRST observation, so a restart can

   never silently "un-de-risk" an already-exhausted split leg.

- An emergency close of one split leg must close the sibling leg.

- Unrelated tickets must never be cross-closed.

- **Status**: FIXED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Phase 08 Continuation Forensic Audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_accounting_hedging.py` (assertion recomputed to the

  correct 3.40 profit factor) + full unit suite

- `src/nexus_scalp/adapters/database/audit_repository.py::get_account_performance_metrics`

- `src/nexus_scalp/web/server.py::get_account_summary` (consumed it)

`get_account_performance_metrics` computed `net = pnl + commission + swap`, i.e. it

ADDED commission and swap back to gross PnL. Commission and swap are COSTS; the

canonical `log_ledger_closed` and `AccountingCore.normalize_trade_row` both compute

`net = gross - commission - swap`. The legacy calculator therefore inflated profits

and disagreed with the canonical accounting core - a second, wrong calculation

engine contradicting the ONE-engine invariant. `/api/account/summary` served those

inflated numbers.

The sign convention used when the ledger was first written (commission/swap as

positive magnitudes to subtract) was not applied in this calculator, and its

`commission` column reads the RAW signed value passed by `log_ledger_closed`

(e.g. -2.0), so the formula must use `abs(commission)` exactly like

`normalize_trade_row` does.

- `tests/unit/test_accounting_hedging.py` seeded commissions as `-2.0`/`-1.0`

  and asserted profit_factor 3.40 (the CORRECT math); the buggy calculator

  returned 3.61 (inflated), failing the assertion.

`net_pnl = pnl - abs(commission) - swap` (swap kept signed - a credit is a credit).

- `get_account_performance_metrics` must agree with `AccountingCore` period

  reports within float tolerance for the same ledger rows.

- The hedging test's 3.40 profit-factor assertion is now the regression guard.

---

- **Status**: FIXED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Phase 08 Continuation Forensic Audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/integration/test_accounting_api.py::TestWorkerWithEngine::test_account_summary_never_serves_synthetic_numbers`

- `src/nexus_scalp/web/server.py::get_account_summary` (`/api/account/summary`)

- `src/nexus_scalp/web/server.py::get_system_state` (`/api/status` account block)

- `Web/app.js` account rendering (null-safe)

`/api/account/summary` returned hardcoded `balance=10000.00`, `equity=10000.00`,

`win_rate=0.0`, `profit_factor=0.0` placeholders whenever the adapter could not be

read or no history existed, and `/api/status` defaulted `account_data` to

`balance=10000.00` / `win_rate=78.5`. This violated the Phase 08 no-synthetic-

numbers invariant on LIVE dashboard endpoints and made failures indistinguishable

from genuine flat results.

Legacy pre-Phase-08 endpoint bodies relied on default constants instead of the

canonical `AccountingCore` facade; the Phase 08 refactor added the facade but did

not rewire these legacy endpoints.

- `/api/account/summary` now reads `AccountingCore.live_state()` + ledger-backed

  totals; unavailable fields are `None`, never placeholders.

- `/api/status` account block defaults to `available=False` with `None` fields and

  reads the real win rate from the canonical core when an engine exists.

- `Web/app.js` renders `n/a` for null account fields instead of crashing on

  `.toFixed()` of `null`/`NaN`.

- No endpoint may return a hardcoded balance/win-rate constant. Any fake-zero

  dashboard value is a regression.

- `test_account_summary_never_serves_synthetic_numbers` asserts real values when

  the adapter is up and None fields when the adapter raises.

---

- **Status**: FIXED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Phase 08 Continuation Forensic Audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_accounting_core.py::TestForensicQualityJoin`

- `src/nexus_scalp/accounting/core.py::_attach_experience_detail`

`_attach_experience_detail` (forensic trade trace quality section) joined the

outcome table with `WHERE e.execution_id = ?` where `e` is `audit_experiences`.

That column is ALWAYS empty by design (immutable decision row written before a

broker ticket exists - see BUG-008). Every forensic trace therefore silently

reported `NO_EXPERIENCE_OUTCOME` and carried an empty quality decomposition even

for fully-attributable trades, and behavioral flags never reached the dashboard.

The BUG-008 join trap was applied a second time in a different function

(`_attach_identity` was fixed, `_attach_experience_detail` was not).

Rewrote the join to go through the outcome table, matching `_attach_identity`:

`WHERE o.execution_id = ?` (`o` = `audit_experience_outcomes`).

- Forensic traces for outcome-linked trades must carry the decomposition columns

  and behavioral flags (regression test asserts strategy/entry/execution/

  management/exit quality and `EARLY_EXIT` round-trip).

- Grep guard: no `WHERE e.execution_id = ?` may exist against

  `audit_experiences` anywhere in `src/`.

---

- **Status**: FIXED

- **Severity**: MEDIUM

- **Confidence**: HIGH

- **Discovered**: Phase 08 Continuation Forensic Audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_intelligence_phase09.py::TestWorkerIsolation::test_worker_checkpoint_persists_across_restart`

- `src/nexus_scalp/intelligence/worker.py` (`IntelligenceWorker`)

- `src/nexus_scalp/adapters/database/audit_repository.py` (schema)

The schema created `intelligence_worker_state`, but NO code ever wrote to or read

from it. The worker's docstring claimed "a checkpoint is recorded so nothing is

rebuilt redundantly", yet a restart simply redid the full cycle from zero state -

the restart-safety story was documentation-only.

Checkpoint persistence was specified but never implemented when the worker was

introduced.

`IntelligenceWorker.start()` now loads the checkpoint (`_load_checkpoint`) and

`stop()` persists it (`_save_checkpoint`), restoring `cycle_count` and the last

autopsy count across restarts. Reads/writes are failure-isolated (a missing table

simply means first run).

- A fresh worker instance must restore `cycle_count >= 1` after a prior

  start/tick/stop against the same database.

---

- **Status**: FIXED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Phase 09B Forensic Audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_research_phase09b.py` (45 tests),

  `tests/integration/test_research_api.py` (7 tests)

- `src/nexus_scalp/intelligence/evolution.py` (`validate_candidate` was only a

  bounded recording API, not a market-data backtest)

- Missing: deterministic backtest, walk-forward, OOS gate, robustness engine,

  multi-dimension scoring, strategy registry.

The prior Phase 09 delivered candidate *discovery* and operator-gated

*promotion*, but the actual market-data backtest harness, walk-forward, OOS,

robustness and scoring engines were never implemented. `validate_candidate`

only recorded an expectancy/sample count supplied by the caller - it did not

evaluate the candidate over historical data, so a "validated" candidate could

not be distinguished from an unbacktested hypothesis on statistical evidence.

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

- Candidate can never bypass RiskEngine / OrderManager / MT5 (tested).

- Pipeline never promotes a candidate to ACTIVE automatically (tested).

- OOS failure forces REJECTED regardless of in-sample/win-rate (tested).

- Small samples never receive high confidence (tested).

- Modified strategy gets a new version; old validation record stays intact

  (tested).

---

- **Status**: FIXED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Phase 10 Forensic Audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_model_lifecycle_phase10.py` (32 tests),

  `tests/integration/test_model_lifecycle_api.py` (7 tests)

- Missing: deterministic training dataset builder, TrainingRun lineage, candidate

  staging paths, validation gates, Champion/Challenger comparison, lifecycle

  status on the model registry, training worker isolation.

The repository had production-grade training infrastructure

(`WalkForwardTrainer`, `ScalpNet`, `experience_model_registry`, schema

registry) but NO controlled-training boundary: nothing prevented a training

run from overwriting the Champion artifact, nothing recorded immutable

TrainingRuns, and there was no candidate/Challenger lifecycle or validation

gate chain. A retrain was effectively an uncontrolled mutation of the

production model path.

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

- Champion artifact hash must stay unchanged across a training run (tested).

- Failed/interrupted training remains FAILED/INCOMPLETE, never VALIDATED.

- No auto-promotion: validated Challenger stays shadow-eligible (tested).

- Schema mismatch (dimension/class/scaler) fails explicitly (tested).

---

- **Status**: FIXED

- **Severity**: CRITICAL

- **Confidence**: HIGH

- **Discovered**: Phase 11 Forensic Audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_shadow_phase11.py` (35/35 green; previously hung)

- `src/nexus_scalp/shadow/store.py` (`_INSERT_DECISION_SQL`)

Every shadow-decision insert failed with `error=31 values for 30 columns`:

the SQL column list had 30 columns but the VALUES clause had 31 `?`

placeholders. The background queue worker logged the error and **dropped the

row**. No shadow decision was ever persisted in production.

The VALUES placeholder count drifted from the column list when the

`hypothetical_pnl_usd` field was added; the mismatch was never caught because

`test_shadow_outcomes_persisted` **hung** on `queue.join()` (see BUG-026) and

was therefore never observed as a failure.

- Column list: 30 names (`shadow_decision_id` .. `payload`).

- VALUES clause: 31 placeholders. Verified by regex count + live repro:

  `Audit Background Worker failed to insert batch error=31 values for 30 columns`.

Attaching a Challenger and running shadow evaluation produced zero persisted

decisions; the comparison and promotion layers had no data to evaluate.

Phase 11 shadow evaluation was completely non-functional at the persistence

layer (silent data loss).

Removed the extra placeholder so 30 values map to 30 columns.

- `test_shadow_outcomes_persisted` now passes (row round-trips through the DB).

`pytest tests/unit/test_shadow_phase11.py` → 35 passed.

- Any INSERT with N columns must have exactly N placeholders; add a

  static placeholder-count smoke test when schema columns change.

---

- **Status**: FIXED

- **Severity**: CRITICAL

- **Confidence**: HIGH

- **Discovered**: Phase 11 Forensic Audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_shadow_phase11.py::TestShadow::test_shadow_outcomes_persisted` (no longer hangs)

- `src/nexus_scalp/adapters/database/audit_repository.py` (`_process_queue_worker`)

When a batch insert failed (e.g. BUG-025's 31-vs-30 placeholder mismatch), the

`except` branch logged and slept but **never called `task_done()`** for the

already-`get()`-ed items. Any subsequent `queue.join()` — including

`AuditRepository.close()` and test teardown — blocked forever. The failed

items were also lost (never re-queued, never persisted).

The error path of the bulk-transaction loop omitted the bookkeeping that the

success path performed.

`test_shadow_outcomes_persisted` hung indefinitely at `repo._queue.join()` with

the worker logging `error=31 values for 30 columns` in a loop.

Any persistent insert error (bad SQL, schema drift, DB lock) permanently

deadlocked every `join()` caller: engine shutdown (`close()`), worker teardown,

and test fixtures.

Unrecoverable hang on shutdown; silent loss of the failed rows.

The error path now also calls `task_done()` for each item in the failed batch

before backing off. `join()` can always return; failed rows are logged as lost

instead of wedging the process.

- `test_shadow_outcomes_persisted` completes instead of hanging.

`pytest tests/unit/test_shadow_phase11.py` → 35 passed.

- `queue.get()` must ALWAYS be paired with `task_done()` in every path

  (success AND failure). Add a failure-injection test for the queue worker.

---

- **Status**: FIXED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Phase 11 Forensic Audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_shadow_phase11.py::TestRegimeStrategy::test_critical_regime_degradation_not_averaged_away`

- `src/nexus_scalp/shadow/comparison.py` (`ShadowComparer.compare`,

  `evaluate_promotion`)

The comparison used `champ_r = [d.hypothetical_r * 1.0 ...]` — the champion's

realized R was numerically identical to the challenger's. Per-regime and

per-strategy deltas were therefore always `0.0`, and the

`degraded_regimes` / `degraded_strategies` / `improved_strategies` /

promotion-veto signals could never fire. Shadow-based promotion evaluation was

statistically meaningless.

`hypothetical_r` is the challenger's realized R on the simulated path. When the

two models disagree on direction, the champion's R on the SAME path has the

opposite sign; the code ignored this and reused the challenger's value.

`test_critical_regime_degradation_not_averaged_away` failed: HIGH_VOLATILITY

delta stayed `0.0` and `degraded_regimes=[]` despite -0.9R challenger outcomes.

A Challenger that collapses in a critical regime shows no degradation signal;

promotion vetoes never trigger; a genuinely worse Challenger could look neutral.

False confidence in Champion/Challenger comparisons; broken promotion

eligibility signals.

- Champion-side R is now derived from the champion's OWN action on the same

  path: identical to `hypothetical_r` when actions agree, `-hypothetical_r`

  when they disagree (one wins, one loses).

- Per-regime/per-strategy/per-session aggregation uses the same derived

  champion-side R.

- Regimes are additionally flagged degraded when the challenger's absolute

  expectancy falls below `MIN_REGIME_EXPECTANCY_R` (0.0), so a bad regime is

  never averaged away by good ones.

- `test_critical_regime_degradation_not_averaged_away` (unchanged) now passes.

`pytest tests/unit/test_shadow_phase11.py` → 35 passed.

- Any "champion vs challenger" numeric comparison must derive each side from

  its OWN action/decision, never proxy one from the other.

- Absolute degradation floors complement relative deltas so critical regimes

  cannot hide behind good ones.

---

- **Status**: FIXED

- **Severity**: MEDIUM

- **Confidence**: HIGH

- **Discovered**: Phase 11 Forensic Audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `tests/unit/test_shadow_phase11.py::TestFailureIsolation` (5/5)

- `src/nexus_scalp/shadow/store.py` (all save/list methods)

- `src/nexus_scalp/model_lifecycle/store.py` (all save/list methods)

A store constructed with `audit_repo=None` raised `AttributeError:

'NoneType' object has no attribute '_is_sqlite'` instead of failing closed /

isolating. The failure-isolation contract (a broken store must never raise

through the engine) was violated.

All guards now use `if not self.audit_repo or not self.audit_repo._is_sqlite:`.

- `test_shadow_db_failure_cannot_stop_trading` (unchanged) now passes.

`pytest tests/unit/test_shadow_phase11.py::TestFailureIsolation` → 5 passed.

- Every persistence guard must be None-safe; a degraded store degrades to

  "return False / no-op", never to an exception.

---

- **Status**: FIXED

- **Severity**: MEDIUM

- **Confidence**: HIGH

- **Discovered**: Phase 11 Forensic Audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `pytest tests/unit/test_shadow_phase11.py` (35/35) + micro-benchmark

- `src/nexus_scalp/shadow/store.py` (`ShadowStore.ensure_schema`)

- `src/nexus_scalp/application/live_engine.py` (`_record_shadow_decision`)

`ShadowStore.save_decision()` -> `ensure_schema()` opened a synchronous

`sqlite3.connect()` + 4 `CREATE TABLE IF NOT EXISTS` + 3 `CREATE INDEX` +

`commit()` on EVERY live tick while a Challenger was attached (~0.65ms per

cycle benchmarked; ~13ms/s at 20 ticks/s). This is blocking DB I/O on the hot

path, violating the "NO Phase 08-11 work may block the live tick path"

invariant. A per-tick `[SHADOW] event=DECISION` info log also spammed output.

`ensure_schema()` is now guarded by an in-process `_schema_ensured` flag: the

DDL runs at most once per process; every subsequent call is a no-op returning

in microseconds. The per-tick decision log was left intact (bounded by active

shadow runs) but no longer has DDL cost behind it.

- Full shadow suite still green (persistence round-trip unchanged).

Micro-benchmark: per-call cost drops from ~0.65ms to ~0.0002ms after the first

call.

- Schema DDL belongs in explicit init paths, never behind a per-record write.

- Any `ensure_schema()` invoked from write paths must be process-guarded.

---

- **Status**: WONT_FIX (documented; low risk given UUID keys)

- **Severity**: LOW

- **Confidence**: HIGH

- **Discovered**: Phase 08-11 Forensic Audit / DB Schema Audit (2026-08-16)

- `src/nexus_scalp/shadow/store.py` (`_INSERT_RUN_SQL`, `_INSERT_DECISION_SQL`,

  `_INSERT_COMPARISON_SQL`, `_INSERT_PROMOTION_SQL`)

- `src/nexus_scalp/model_lifecycle/store.py` (`_INSERT_RUN_SQL`,

  `_INSERT_COMPARISON_SQL`)

- `src/nexus_scalp/intelligence/evolution.py` (`strategy_evolution_candidates`)

Six phase tables documented as "append-only / immutable" use

`INSERT OR REPLACE` on UNIQUE keys. REPLACE = DELETE+INSERT, which rewrites

the AUTOINCREMENT row id and can in principle orphan cross-table references

(e.g. `model_comparisons.run_id`), contradicting the immutability claim.

The correct append-only pattern used elsewhere is `INSERT ... ON CONFLICT

DO NOTHING` (experiences, lifecycle events).

Low in practice: all writes carry freshly generated UUID keys

(`shadow_<hex>`, `run_<hex>`, `sd_<hex>`), so REPLACE virtually never

collides; row-id churn is invisible to consumers who key on the UUID.

No data loss occurs.

Switch these to `ON CONFLICT(run_id/shadow_decision_id) DO NOTHING` when

next touching these files. Not a production-safety defect today.

---

- **Status**: WONT_FIX (documented; future-schema readiness item)

- **Severity**: MEDIUM

- **Confidence**: HIGH

- **Discovered**: Phase 08-11 Forensic Audit / DB Schema Audit (2026-08-16)

- `src/nexus_scalp/shadow/store.py::ensure_schema`

- `src/nexus_scalp/model_lifecycle/store.py::ensure_schema`

- research/intelligence phase stores

Phase stores create tables with `CREATE TABLE IF NOT EXISTS` + indexes only.

If a phase table already exists with an older column set, a later release

adding columns silently does nothing — the INSERT then fails with

`no such column` at write time, and the queued worker drops the whole batch

(BUG-026 path). audit_repository.py handles this with defensive

`ALTER TABLE ... ADD COLUMN` in try/except; phase stores do not.

Future schema evolution of phase tables (e.g. 60D/350D feature schemas)

requires a manual migration step or a hard DROP + recreate (data loss).

Not a defect in the current 50D schema.

Centralize schema migration: a shared `_add_columns_if_missing(conn, table,

cols)` helper used by every phase store before writes, or a schema_version

table with explicit migrations.

---

- **Status**: WONT_FIX (bounded-by-design; documented)

- **Severity**: LOW

- **Confidence**: HIGH

- **Discovered**: Phase 08-11 Forensic Audit / DB Schema Audit (2026-08-16)

- `src/nexus_scalp/adapters/database/audit_repository.py` (`log_signal`,

  `_process_queue_worker`)

1. `_queue.put_nowait` on a full queue (maxsize 10000) drops the record with

   only a log line — no spill, no retry. Bounded and intentional (hot-path

   protection), but a 10k-row burst loses telemetry.

2. `audit_signals` dedup is a single in-memory `_last_logged_signal_key`

   (protects consecutive duplicates only; lost on restart) — no UNIQUE

   constraint on the table, so restart/worker-crash can produce duplicate

   signal rows.

Observability loss under extreme bursts; duplicate signal rows after crash.

Neither affects trading decisions (audit is telemetry).

Add a SQL-level dedup (e.g. UNIQUE index on the 5-tuple) or accept the

bounded-loss contract and document it in skill.md (already documented as

"queue full -> drop telemetry").

---

- **Status**: FIXED (2026-08-16, Phase 12 completion)

- **Severity**: MEDIUM

- **Confidence**: HIGH

- **Discovered**: Phase 12 forensic audit (live code inspection)

`CurrentNewsContextCache.get()` rebuilt the context (a synchronous SQLite

`SELECT * FROM news_analysis ORDER BY analyzed_at DESC LIMIT 100`) whenever

the 60s TTL expired — including when called from

`LiveEngine._process_tick_pipeline()` via `news_engine.current_context()`.

Violates the "no DB access on the live tick path" invariant.

`src/nexus_scalp/news/context.py` — `get()` had `if (now_mono - last)<ttl: return cached; self._context = self.build()` (build → `db.list_analysis`).

`live_engine.py` `_process_tick_pipeline` called `current_context()` per tick.

The tick path triggered the TTL-expired rebuild itself instead of relying on

the background worker to refresh the context off-loop.

- `NewsContextCache.get()` is now **cache-only** on the live path (returns

  the cached object or a safe first-run default; NEVER touches the DB).

- New `NewsContextCache.refresh()` rebuilds from DB and is called by the

  NewsWorker cycle (`asyncio.to_thread`) and by `engine.self_heal()` /

  explicit API requests (`force=True`) only.

- `NewsEngine.current_context(force=True)` remains for API/worker/self-heal.

- Unit: `test_53_context_cache_bounded` passes (cache returns same timestamp).

- Full regression: 406 unit + 56 integration tests green.

- Live path now reads only an in-memory object per tick.

---

- **Status**: FIXED (2026-08-16, Phase 12 completion)

- **Severity**: MEDIUM

- **Confidence**: HIGH (verified by live HTTP checks)

- **Discovered**: Phase 12 source-reachability audit

Several Tier-1 official source URLs were dead, causing the fetcher to

silently fail every cycle for those sources (no articles, no error surfaced).

- BEA `https://www.bea.gov/rss/news` → HTTP 404

- CFTC `https://www.cftc.gov/RSS/CFTC_RSS.xml` → HTTP 404 (no public CFTC

  RSS exists; RSS/rss.aspx also 404)

- U.S. Treasury `https://home.treasury.gov/rss/press-releases.xml` → HTTP 503

- Working alternatives verified: `https://www.bea.gov/news` (200),

  `https://home.treasury.gov/news/press-releases` (200)

- `seed.py` (SEED_VERSION bumped to `2026-08-16-v2`): BEA and Treasury now

  point at the verified live pages (HTML extraction path); CFTC registered

  but **disabled by default** (`enabled: False`) since no public feed exists.

- The fetcher health tracker continues to mark unreachable sources

  unhealthy/backed-off after consecutive failures instead of silent empty.

- Seed idempotency test (`test_50_seed_idempotent`) passes.

- `test_07_source_disablement` passes.

- Fresh DB: 10 enabled sources, CFTC disabled; unit suite green (406).

---

- **Status**: FIXED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Release Engineering build (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: packaged EXE `version --plain` / `health --json` return the Typer CLI output

- `scripts/build/build_release.ps1` / CI `release.yml` (PyInstaller entrypoint)

- `src/nexus_scalp/release/packaged_main.py` (new)

The first PyInstaller onedir build used `NexusTradingForexBot.py` as the

entrypoint. PyInstaller packages that script as `__main__`, so the EXE exposed

the argparse launcher (`--config/--doctor/--gateway/--symbol`) and rejected

`version --plain` / `health --json` with "unrecognized arguments". The

packaged product had no release CLI surface at all.

The launcher entrypoint predates the release CLI; the release build reused it

without checking what CLI surface it exposes.

New `src/nexus_scalp/release/packaged_main.py` — a PyInstaller entrypoint that

delegates to the Typer `nexus` app. Both build paths (local ps1 + CI) now

build from it.

- `tests/unit/test_release_build_system.py::test_build_scripts_reference_packaged_entrypoint`

- packaged EXE smoke (build_release.ps1 + verify_release.ps1) asserts

  `version --plain` and `health --json` succeed.

---

- **Status**: FIXED

- **Severity**: MEDIUM

- **Confidence**: HIGH

- **Discovered**: Installer smoke test (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `clean_install_test.ps1` silent install → uninstall passes (exit 0)

- `installer/NexusScalpEngine.iss` (custom uninstall wizard page)

`unins000.exe /VERYSILENT` exited 1: the custom `CreateInputOptionPage`

("preserve your data?") required input pages in non-interactive uninstall.

Automated/CI uninstalls therefore failed and could leave the app installed.

The uninstall wizard page was shown unconditionally; silent mode has no way to

answer it.

Deletion of user data now happens only when `not UninstallSilent` AND the

checkbox is ticked. Silent uninstall always preserves user data (exit 0).

- Installer smoke: silent install → reinstall → uninstall all exit 0.

- User data under `{localappdata}\NexusScalpEngine` preserved after

  uninstall (checked by `clean_install_test.ps1`).

---

- **Status**: FIXED

- **Severity**: HIGH

- **Confidence**: HIGH

- **Discovered**: Release Engineering dependency audit (2026-08-16)

- **Fixed**: 2026-08-16

- **Verified**: `pip install -e .[web,release]` + packaged EXE launches web server deps

- `pyproject.toml` `[project] dependencies` + `[project.optional-dependencies]`

- `requirements.txt`

`fastapi`, `uvicorn`, `httpx` were required at runtime (web server, news

ingestion) but declared nowhere in `pyproject.toml` dependencies —

`ci.yml` papered over this with a manual `pip install fastapi uvicorn httpx`.

`feedparser` (used by the Phase 12 news sources) was also undeclared. Any

clean install or packaged build without the manual pip step silently lacked

the web/news runtime.

Dependency declarations drifted from the runtime import graph (the `web`

extra was referenced by ci.yml but never defined).

- Added `fastapi`, `uvicorn`, `httpx` (+ `feedparser` conditional) to core deps,

  defined `web` and `release` extras (pyinstaller), and mirrored the runtime

  list in `requirements.txt`.

- `ci.yml` now installs `.[dev,web]` with no manual fallback.

- `tests/unit/test_release_build_system.py::test_requirements_cover_web_and_news_runtime`

- fresh venv `pip install -e .` pulls the full runtime.

---

- **Status**: FIXED (2026-08-16, Phase 13 migration)

- **Severity**: HIGH (news-aware models could not be reloaded)

- **Confidence**: HIGH (proven by integration test)

- **Discovered**: Phase 13 model-generation migration (runtime load path)

A candidate trained with `news_enabled=true` (50 base features + 12 news

dims = 62 inputs) could not be loaded by `LocalModelRuntime`: the runtime

reconstructed the model with `input_dim = feature_dimension (50)`, so the

state_dict load failed with a shape mismatch. `SampleReplay` also predicted

with the wrong width.

`training.py` wrote `feature_dimension=len(feat_cols)` (50) while the model

was built with `input_dim = len(feat_cols)+len(news_cols)` (62). The runtime

had no record of the actual neural input width.

The manifest stored only the BASE feature schema dimension; the extra news

dimensions were implicit in the state dict shape but not recorded.

- `training.py`: `build_metadata["input_dimension"]` records the exact

  neural input width (base + news).

- `runtime.py`: model construction + `predict()` input validation use

  `input_dimension`; a manifest whose `input_dimension < feature_dimension`

  or (news disabled yet dims differ) is REJECTED as corrupted.

- `replay.py`: when the model is news-aware, the replay appends the sample's

  news context vector (schema order) before predicting.

- `tests/integration/test_model_generation.py::test_full_artifact_flow` —

  news-aware candidate loads + predicts with DB import blocked.

- `tests/unit/test_model_generation_phase13.py::test_36/37/38` — corrupted

  manifests and narrowed schemas now raise `ManifestValidationError`.

- All 55 Phase 13 tests pass.

---

- **Status**: DOCUMENTED (by design — legacy bridge, not a label)

- **Severity**: LOW

- **Confidence**: HIGH

- **Discovered**: Phase 13 migration (label contract audit)

The labeler is 3-class (NO_TRADE/BUY/SELL; WAIT is policy-derived) but the

legacy ScalpNet head outputs 4 logits (0=NO_TRADE,1=BUY,2=SELL,3=WAIT).

Without an explicit contract this looks like a bug.

Legacy architecture: the 4th logit is a POLICY bridge (WAIT state), never a

training label. Phase 10's `EXPECTED_NUM_CLASSES=4` encoded this implicitly.

Phase 13 makes the contract explicit:

- `LabelSchema` (`triple_barrier_3class_v1`): class_count=3, WAIT is NOT a

  label — `schema.encode("WAIT")` raises.

- `ModelManifest.class_count=3` + `classes=[NO_TRADE,BUY_MARKET,SELL_MARKET]`;

  `ModelFactory` keeps the legacy 4-head geometry ONLY for

  `LEGACY_SCALPNET_V1` (with an explicit comment) and 3 heads for all new

  architectures.

- Tests enforce: label layer rejects class 3; runtime decode maps argmax 3 →

  policy WAIT in the legacy baseline only.

- `test_09_label_mismatch_rejected`, `test_22_3class_label_contract_enforced`.

---

- **Status**: FIXED (2026-08-16, Phase 13 forensic supervision audit)

- **Severity**: MEDIUM (security; no exploit in production since ids are

  generated internally, but the public API was unsafe)

- **Confidence**: HIGH

- **Discovered**: Forensic audit T03/T58 (path traversal review)

`ArtifactStore.model_dir(model_id)` / `dataset_dir(dataset_id)` /

`experiment_path(experiment_id)` concatenated the raw id into a `Path`

without validation. A model_id like `../champion` or `../../etc` would

resolve OUTSIDE the artifact root.

`artifact_store.py` — `return self.models_dir / model_id` with no

sanitization; id values come from callers (CLI `--model`, tests, API).

No identifier validation at the store boundary; ids assumed trusted.

`validate_artifact_id()`: only `[A-Za-z0-9_.-]`, no `..`, no path

separators; applied to model/dataset/experiment path builders. Invalid ids

raise `ValueError`.

`test_board_path_traversal_rejected` + `test_board_store_refuses_traversal_through_api`

— traversal ids raise; safe ids accepted.

---

- **Status**: FIXED (2026-08-16, Phase 13 forensic supervision audit)

- **Severity**: HIGH (training↔inference distribution mismatch)

- **Confidence**: HIGH

- **Discovered**: Forensic audit T24 (scaler/preprocessor)

`CandidateTrainer.train_candidate` trained on UN-normalized raw features

and saved artifacts with `scaler=None`. The manifest declared `scaler_hash`

as `""` while the legacy WalkForwardTrainer (and the production champion

path) always fits + persists a scaler (mean/std). A model-generation

candidate therefore had no reproducible distribution transform; the runtime

silently skipped scaling.

`training.py` line ~194 `scaler=None`; `runtime.py` scaling block gated on

`self._scaler is not None` (silent skip).

The artifact-first pipeline had not wired the train-fitted scaler that the

rest of the system treats as an invariant.

- `CandidateTrainer`: fits mean/std on the TRAIN split ONLY (zero leakage

  into val/OOS), trains + evaluates on scaled features, persists the scaler

  with the artifact (fixing an `np.savez` path bug found in the same sweep,

  BUG-040).

- `LocalModelRuntime.load`: if the manifest DECLARES a scaler hash but the

  scaler file is missing/corrupt, loading FAILS (no silent unscaled

  prediction).

`test_board_scaler_persisted_and_roundtrips` (scaler file + hash +

deterministic scaled prediction), `test_board_missing_declared_scaler_blocks_load`

(missing scaler with declared hash -> ManifestValidationError).

---

- **Status**: FIXED (2026-08-16, Phase 13 forensic supervision audit)

- **Severity**: MEDIUM (crashed every scaler save once scaler wiring landed)

- **Confidence**: HIGH

- **Discovered**: Forensic audit T06/T24 (exercised the new scaler path)

`np.savez(tmp_path, ...)` appends `.npz` to its path argument; the code then

attempted `tmp_path.replace(final_path)` on a file that did not exist

(`scaler.npz.tmp`), raising `FileNotFoundError` (WinError 2) on every model

save that included a scaler.

`artifact_store.py` `save_model_artifact` scaler branch:

`np.savez(tmp_s, ...)` then `tmp_s.replace(scaler_path)` — the actual file

written was `tmp_s + ".npz"`.

`np.savez`'s implicit `.npz` suffix was not accounted for.

Save to `scaler.tmp` (no suffix), then atomically rename

`scaler.tmp.npz` -> `scaler.npz`, with `finally` cleanup of both leftovers.

All Phase 13 training tests pass with the scaler persisted

(`test_board_scaler_persisted_and_roundtrips`), no `.tmp`/`tmp.npz`

leftovers (audit T03 concurrency/atomicity check).

---

- **Status**: FIXED (2026-08-16, Phase 13 forensic supervision audit, round 2)

- **Severity**: HIGH (a NaN-trained model could reach CHALLENGER eligibility)

- **Confidence**: HIGH (proven by adversarial probe)

- **Discovered**: Forensic audit T29 (failed-training simulation)

A dataset containing NaN/Inf feature values trained successfully: `status="COMPLETED"` with `val_acc=0.0000`. NaN loss propagates silently through the optimizer → a numerically garbage model that looks trained. Violates the invariant "failed training is FAILED, never CHALLENGER".

Adversarial probe with `feat_0=[nan,1,2]` + `MLP_V2` produced

`[TRAIN] event=CANDIDATE_READY val_acc=0.0000`. The training path never

validated input finiteness.

No finite-input gate before the training loop; NaN/Inf features flow

straight into loss.backward().

`CandidateTrainer.train_candidate` now rejects non-finite feature matrices

up front: `if not np.isfinite(X_arr).all(): return {"status": "FAILED", ...}`.

(The 3-class label schema already rejects invalid label values.)

`test_board_nan_features_fail_training`, `test_board_inf_features_fail_training`,

`test_board_nan_labels_fail_training` — all assert FAILED + reason.

---

- **Status**: FIXED

- **Severity**: MEDIUM

- **Confidence**: HIGH

- **Discovered**: Runtime test pass (2026-08-16 hardening)

- **Fixed**: 2026-08-16

- **Verified**: `tests/runtime/test_packaged_cli.ps1` — `--help` now exits 0

- `src/nexus_scalp/release/cli_shim.py`

The onefile `NexusScalpEngine-CLI.exe --help` exited 1 instead of 0. The

source interpreter path (`python cli_shim.py --help`) exited 0, so the defect

only appeared in the frozen PyInstaller build — the exact class of packaging

bug the runtime test suite exists to catch.

The app-level Typer help string contained a U+2014 EM DASH

("Nexus Trading Forex Bot — operational [and] release console"). The frozen

onefile console encodes output in the active code page; the em dash maps to

`<undefined>` and the script aborted with `unhandled exception` + a code-page

error, exiting 1. The `sys.exit(app())` wrap was a contributing factor but not

the primary defect.

- Replaced non-ASCII characters (em dash, arrow) in every Typer `help=`

  string with ASCII-safe equivalents (`-`, `to`).

- `cli_shim.py` now calls `app()` directly and lets Typer's own `SystemExit`

  propagate (kept as defence-in-depth; documented in the module docstring).

- `tests/runtime/test_packaged_cli.ps1` (`--help` exits 0).

- `test_cli_version_and_help` asserts help/version exit 0.

# Top 20 Problems
1. Lost request_id on restart.
2. Audit Queue deadlock.
3. Account Identity Safety Gate Bypass in MT5 Adapter.
4. Order Dispatch Retry lacking Existence Verification.
5. N+1 Queries in Position Accounting.
6. Execution Mode Display Stale.
7. Degenerate Shadow R.
8. Hot-Path Sync I/O in LiveEngine.
9. Model Contract Ambiguity.
10. Legacy `feat_0`..`feat_17` block in CLI.
11. Missing terminal outcomes.
12. Chart aggregator duplicated the currently-forming bar on cold restart.
13. Negative MFE recorded for SELL trades.
14. DNS Poisoning on api.telegram.org.
15. Catch-all exceptions exposing stack traces.
16. Hardcoded LLM generation prompts.
17. Relative degradation checks divided by near-zero.
18. Synthetic News in BenchmarkRunner.
19. Unclamped inverse class frequency.
20. CandidateTrainer seeded RNG after model construction.

# Top 20 Repairs
1. Implement Deterministic ID Correlation.
2. Ensure Queue `task_done()`.
3. Add `orders_get` verification in MT5 retry loop.
4. Inject strict account matching in `connect()`.
5. Refactor Position Accounting to use bulk inserts.
6. Sync Execution Mode Display from backend.
7. Calculate independent champion R.
8. Audit and remove sync I/O from tick pipeline.
9. Validate runtime datasets against actual models.
10. Remove legacy 18D training logic.
11. Emit `REJECTED_UNFILLED` or `CANCELED` for undispatched orders.
12. Implement `BarAggregator.reseed`.
13. Seed absolute price diffs at 0.0.
14. Fallback to direct IPs with SNI preserved for Telegram.
15. Use standardized `safe_error_payload` envelope.
16. Expose LLM configurations via `factory.llm_*`.
17. Implement epsilon-floor division.
18. Require actual SQLite news DB.
19. Clamp inverse weights to [0.5, 2.0].
20. Hoist `torch.manual_seed()` above `build()`.

# Final Verification Checklist
* [x] Repository inventory completed
* [x] All major projects inspected
* [x] Entry points identified
* [x] Critical paths mapped
* [x] Architecture dependencies traced
* [x] Dead code audited
* [x] Duplicate code audited
* [x] Obsolete code audited
* [x] No-effect code audited
* [x] Async candidates audited
* [x] Async anti-patterns audited
* [x] Concurrency audited
* [x] Performance audited
* [x] Memory audited
* [x] Database audited
* [x] Persistence audited
* [x] Network audited
* [x] Error handling audited
* [x] Silent failures audited
* [x] Fake success audited
* [x] False confidence audited
* [x] Configuration audited
* [x] Hardcoded behavior audited
* [x] State machines audited
* [x] Dependency injection audited
* [x] Lifecycle audited
* [x] Startup/shutdown audited
* [x] Restart safety audited
* [x] Data integrity audited
* [x] Time integrity audited
* [x] Market data audited
* [x] Model contracts audited
* [x] Feature pipeline audited
* [x] Registry/serving alignment audited
* [x] Execution audited
* [x] Risk enforcement audited
* [x] UI/backend integrity audited
* [x] Display-only behavior audited
* [x] Strategy Factory audited
* [x] Validation audited
* [x] Backtesting audited
* [x] Empirical replay audited
* [x] Historical simulation audited
* [x] Mode separation audited
* [x] Simulation poisoning audited
* [x] Lookahead audited
* [x] WFO/OOS audited
* [x] Robustness audited
* [x] Reproducibility audited
* [x] Testing gaps identified
* [x] Security-relevant areas reviewed
* [x] External dependencies reviewed
* [x] Observability reviewed
* [x] Recovery reviewed
* [x] Critical repairs prioritized
* [x] Repair dependencies mapped
* [x] Validation plans defined
* [x] What-not-to-change documented
* [x] Uncertain findings explicitly marked
* [x] No source code modified
* [x] No additional repository files created

# Final System Verdict
HEALTHY WITH TECHNICAL DEBT. The system is structurally sound but requires immediate P0 fixes to avoid state corruption during unpredictable restarts.